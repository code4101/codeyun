from __future__ import annotations

import csv
import json
import os
import re
import struct
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root, resolve_fanxiu_resource_root


FANXIU_APK_UNPACKED_ROOT_ENV = "FANXIU_APK_UNPACKED_ROOT"
DEFAULT_FANXIU_APK_UNPACKED_ROOT = Path(
    r"D:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked"
)
DEFAULT_TAPTAP_DOWNLOAD_DAT_PATH = Path(r"D:\TapTap\Support\android_emulator\download\101000000.dat")

APK_INDEX_DEFAULT_KEYWORDS = (
    "UnityPlayerActivity",
    "UnityPlayer",
    "loadLibrary",
    "il2cpp",
    "tolua",
    "Lua",
    "AssetBundle",
    "filelist",
    "resdownload",
    "download",
    "hotfix",
    "patch",
    "version",
    "md5",
    "encrypt",
    "decrypt",
    "http",
    "https",
    "cdn",
    "frxx",
    "gongfa",
    "resource",
    "config",
    "功法",
    "玄魔",
    "法宝",
    "仙侣",
    "资源",
    "下载",
    "加密",
    "解密",
    "热更",
)

APK_RUNTIME_ENTRY_SCAN_KEYWORDS = (
    "frxx",
    "akbing",
    "eyugame",
    "cdn",
    "config",
    "server",
    "bulletin",
    "login",
    "download",
    "resdownload",
    "GameStart.unity",
    "GameResDownLoad.unity",
    "GameEmpty.unity",
    "MU.GameLogic.GameResDownLoad",
    "GameResDownloadBridge",
    "LuaBridge.Load",
    "AssetBundleEncryptStream",
    "CoroutineHttpLoader",
    "HttpDownload",
    "HttpLoader",
    "AssetBundle",
    "tolua",
    "Lua",
)

_APK_RUNTIME_URL_TERMS = (
    "frxx",
    "akbing",
    "eyugame",
    "resdownload",
    "xiuxian",
    "mobi37",
    "api-login",
)

_APK_RUNTIME_SYMBOL_NEEDLES = (
    ("unity_boot_scene", "GameStart.unity", "启动场景"),
    ("unity_boot_scene", "GameResDownLoad.unity", "资源下载场景"),
    ("unity_boot_scene", "GameEmpty.unity", "空场景/占位场景"),
    ("unity_runtime_symbol", "MU.GameLogic.GameResDownLoad", "资源下载逻辑类名"),
    ("unity_runtime_symbol", "GameResDownloadBridge", "资源下载桥接类名"),
    ("unity_runtime_symbol", "LuaBridge.Load", "Lua 加载桥接入口"),
    ("unity_runtime_symbol", "AssetBundleEncryptStream", "AssetBundle 加密流"),
    ("unity_runtime_symbol", "CoroutineHttpLoader", "协程 HTTP 加载器"),
    ("unity_runtime_symbol", "HttpDownload", "HTTP 下载器"),
    ("unity_runtime_symbol", "HttpLoader", "HTTP 加载器"),
)

_APK_RUNTIME_ASSET_PATH_TERMS = (
    "resdownload",
    "gongfa",
    "faze",
    "skill",
    "lua",
    "config",
)

_APK_RUNTIME_CATEGORY_LIMITS = {
    "config_url": 120,
    "unity_boot_scene": 20,
    "unity_runtime_symbol": 80,
    "native_bridge": 30,
    "android_shell": 80,
    "il2cpp_symbol": 120,
    "asset_path": 160,
}

_APK_RUNTIME_CATEGORY_ORDER = {
    "config_url": 0,
    "unity_boot_scene": 1,
    "unity_runtime_symbol": 2,
    "native_bridge": 3,
    "il2cpp_symbol": 4,
    "android_shell": 5,
    "asset_path": 6,
}

_URL_BYTES_RE = re.compile(rb"https?://[A-Za-z0-9./?&_%=:#@+\-~]+")
_PRINTABLE_BYTES_RE = re.compile(rb"[\x20-\x7E]{4,500}")

_AXML_NO_INDEX = 0xFFFFFFFF
_AXML_XML_TYPE = 0x0003
_AXML_STRING_POOL_TYPE = 0x0001
_AXML_START_ELEMENT_TYPE = 0x0102
_AXML_END_ELEMENT_TYPE = 0x0103
_AXML_UTF8_FLAG = 0x00000100

_MANIFEST_COMPONENT_TAGS = ("activity", "activity-alias", "service", "receiver", "provider")

_BINARY_SCAN_SKIP_SUFFIXES = {
    ".apk",
    ".arsc",
    ".bnk",
    ".dat",
    ".dex",
    ".jpg",
    ".jpeg",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".so",
    ".ttf",
    ".wem",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _clean_tsv_cell(value: object, *, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", " ").replace("\x00", "")
    if len(text) > limit:
        text = f"{text[:limit]}..."
    return text


def _write_tsv(path: Path, fields: list[str], rows: Iterable[dict[str, object]]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _clean_tsv_cell(row.get(field, "")) for field in fields})
            count += 1
    return count


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        return [dict(row) for row in csv.DictReader(f, delimiter="\t")]


def _read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise FanxiuResourceError(f"DEX 偏移越界：{offset}")
    return struct.unpack_from("<I", data, offset)[0]


def _read_uleb128(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    pos = offset
    while True:
        if pos >= len(data):
            raise FanxiuResourceError(f"DEX uleb128 越界：{offset}")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return result, pos
        shift += 7
        if shift > 35:
            raise FanxiuResourceError(f"DEX uleb128 过长：{offset}")


def _get_seq(seq: list[str], index: int, default: str = "") -> str:
    return seq[index] if 0 <= index < len(seq) else default


def _descriptor_to_java_name(descriptor: str) -> str:
    primitives = {
        "V": "void",
        "Z": "boolean",
        "B": "byte",
        "S": "short",
        "C": "char",
        "I": "int",
        "J": "long",
        "F": "float",
        "D": "double",
    }
    if descriptor in primitives:
        return primitives[descriptor]
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return descriptor[1:-1].replace("/", ".")
    if descriptor.startswith("["):
        return descriptor.replace("/", ".")
    return descriptor.replace("/", ".")


def _parse_dex(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if not data.startswith(b"dex\n"):
        raise FanxiuResourceError(f"不是有效 DEX 文件：{path}")

    file_size = _read_u32(data, 32)
    string_ids_size = _read_u32(data, 56)
    string_ids_off = _read_u32(data, 60)
    type_ids_size = _read_u32(data, 64)
    type_ids_off = _read_u32(data, 68)
    method_ids_size = _read_u32(data, 88)
    method_ids_off = _read_u32(data, 92)
    class_defs_size = _read_u32(data, 96)
    class_defs_off = _read_u32(data, 100)

    strings: list[str] = []
    for index in range(string_ids_size):
        string_data_off = _read_u32(data, string_ids_off + index * 4)
        _utf16_size, cursor = _read_uleb128(data, string_data_off)
        end = data.find(b"\x00", cursor)
        if end < 0:
            end = min(len(data), cursor + 4096)
        strings.append(data[cursor:end].decode("utf-8", errors="replace"))

    type_descriptors: list[str] = []
    for index in range(type_ids_size):
        string_idx = _read_u32(data, type_ids_off + index * 4)
        type_descriptors.append(_get_seq(strings, string_idx))

    methods: list[dict[str, str | int]] = []
    for index in range(method_ids_size):
        offset = method_ids_off + index * 8
        if offset + 8 > len(data):
            raise FanxiuResourceError(f"DEX method_ids 越界：{path}")
        class_idx, _proto_idx, name_idx = struct.unpack_from("<HHI", data, offset)
        descriptor = _get_seq(type_descriptors, class_idx)
        class_name = _descriptor_to_java_name(descriptor)
        name = _get_seq(strings, name_idx)
        methods.append(
            {
                "index": index,
                "class_descriptor": descriptor,
                "class_name": class_name,
                "name": name,
                "qualified_name": f"{class_name}.{name}" if class_name and name else name,
            }
        )

    class_descriptors: list[str] = []
    for index in range(class_defs_size):
        offset = class_defs_off + index * 32
        class_idx = _read_u32(data, offset)
        class_descriptors.append(_get_seq(type_descriptors, class_idx))

    return {
        "path": path,
        "name": path.name,
        "file_size": file_size or len(data),
        "strings": strings,
        "types": type_descriptors,
        "methods": methods,
        "classes": class_descriptors,
        "summary": {
            "dex": path.name,
            "file_size": file_size or len(data),
            "string_count": len(strings),
            "type_count": len(type_descriptors),
            "method_count": len(methods),
            "class_count": len(class_descriptors),
        },
    }


def _value_keyword_hits(
    *,
    kind: str,
    source: str,
    value: str,
    keywords: tuple[str, ...],
    seen: set[tuple[str, str, str, str]],
) -> Iterable[dict[str, str]]:
    normalized = value.lower()
    for keyword in keywords:
        if keyword.lower() not in normalized:
            continue
        key = (kind, source, keyword, value)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "kind": kind,
            "source": source,
            "keyword": keyword,
            "value": value,
        }


def resolve_fanxiu_apk_unpacked_root(apk_root: str | os.PathLike[str] | None = None) -> Path:
    value = apk_root or os.environ.get(FANXIU_APK_UNPACKED_ROOT_ENV) or DEFAULT_FANXIU_APK_UNPACKED_ROOT
    root = Path(value).expanduser().resolve()
    if not root.exists():
        raise FanxiuResourceError(f"APK 解包目录不存在：{root}")
    if not root.is_dir():
        raise FanxiuResourceError(f"APK 解包路径不是目录：{root}")
    has_dex = any(root.glob("classes*.dex"))
    has_manifest = (root / "AndroidManifest.xml").exists()
    if not has_dex and not has_manifest:
        raise FanxiuResourceError(f"目录不像 APK 解包目录，缺少 classes*.dex 或 AndroidManifest.xml：{root}")
    return root


def _read_key_value_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _iter_apk_files(root: Path) -> Iterable[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda item: item.relative_to(root).as_posix().lower())


def _write_apk_files(root: Path, output_dir: Path) -> dict[str, Any]:
    suffix_counts: Counter[str] = Counter()
    top_dir_counts: Counter[str] = Counter()
    total_bytes = 0

    def rows() -> Iterable[dict[str, object]]:
        nonlocal total_bytes
        for path in _iter_apk_files(root):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = path.relative_to(root)
            rel_parts = rel.parts
            top_dir = rel_parts[0] if len(rel_parts) > 1 else "."
            suffix = path.suffix.lower() or "<none>"
            suffix_counts[suffix] += 1
            top_dir_counts[top_dir] += 1
            total_bytes += size
            yield {
                "relative_path": rel.as_posix(),
                "size": size,
                "suffix": suffix,
                "top_dir": top_dir,
            }

    count = _write_tsv(output_dir / "apk_files.tsv", ["relative_path", "size", "suffix", "top_dir"], rows())
    return {
        "file_count": count,
        "total_bytes": total_bytes,
        "suffix_counts": dict(suffix_counts.most_common()),
        "top_dir_counts": dict(top_dir_counts.most_common()),
    }


def _native_lib_role(name: str) -> str:
    lower = name.lower()
    if lower == "libil2cpp.so":
        return "unity-il2cpp"
    if lower == "libunity.so":
        return "unity-runtime"
    if "tolua" in lower or "lua" in lower:
        return "lua-bridge"
    if "wwise" in lower or "ak" in lower:
        return "audio"
    return "native"


def _write_native_libs(root: Path, output_dir: Path) -> int:
    rows: list[dict[str, object]] = []
    lib_root = root / "lib"
    if lib_root.is_dir():
        for path in sorted(lib_root.glob("*/*.so"), key=lambda item: item.relative_to(root).as_posix().lower()):
            rows.append(
                {
                    "abi": path.parent.name,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "role": _native_lib_role(path.name),
                    "relative_path": path.relative_to(root).as_posix(),
                }
            )
    return _write_tsv(output_dir / "native_libs.tsv", ["abi", "name", "size", "role", "relative_path"], rows)


def _unity_file_role(path: Path) -> str:
    lower = path.as_posix().lower()
    name = path.name.lower()
    if name == "global-metadata.dat":
        return "il2cpp-metadata"
    if name == "boot.config":
        return "unity-boot-config"
    if name == "globalgamemanagers":
        return "unity-global-managers"
    if "sharedassets" in name:
        return "unity-shared-assets"
    if "resources.assets" in name:
        return "unity-resources"
    if "/managed/" in lower:
        return "unity-managed"
    return "unity-data"


def _write_unity_files(root: Path, output_dir: Path) -> int:
    rows: list[dict[str, object]] = []
    data_root = root / "assets" / "bin" / "Data"
    if data_root.is_dir():
        for path in sorted((item for item in data_root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix().lower()):
            rows.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "role": _unity_file_role(path.relative_to(root)),
                }
            )
    return _write_tsv(output_dir / "unity_files.tsv", ["relative_path", "size", "role"], rows)


def _write_asset_filelists(root: Path, output_dir: Path) -> int:
    filelist_specs = [
        ("filelist", root / "assets" / "filelist.csv"),
        ("filelist_streaming", root / "assets" / "filelist_streaming.csv"),
    ]
    rows: list[dict[str, object]] = []
    for kind, path in filelist_specs:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(
                    {
                        "kind": kind,
                        "path": row.get("path") or row.get("file") or "",
                        "size": row.get("size") or "",
                        "md5": row.get("md5") or "",
                        "package": row.get("package") or "",
                    }
                )
    return _write_tsv(output_dir / "asset_filelist.tsv", ["kind", "path", "size", "md5", "package"], rows)


def _read_filelist_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rel_path = str(row.get("path") or row.get("file") or "").strip().replace("\\", "/")
            if not rel_path:
                continue
            try:
                size = int(row.get("size") or 0)
            except ValueError:
                size = 0
            rows.append(
                {
                    "path": rel_path,
                    "size": size,
                    "md5": str(row.get("md5") or "").strip(),
                    "package": str(row.get("package") or "").strip() or "<blank>",
                }
            )
    return rows


def _read_asset_filelist_rows(root: Path) -> list[dict[str, object]]:
    return _read_filelist_rows(root / "assets" / "filelist.csv")


def _read_streaming_file_set(root: Path) -> set[str]:
    path = root / "assets" / "filelist_streaming.csv"
    if not path.is_file():
        return set()
    result: set[str] = set()
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rel_path = str(row.get("file") or "").strip().replace("\\", "/")
            if rel_path:
                result.add(rel_path)
    return result


def _path_top_dir(rel_path: str) -> str:
    return rel_path.split("/", 1)[0] if "/" in rel_path else "."


def _path_suffix(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    return suffix or "<none>"


def _counter_preview(counter: Counter[str], limit: int = 8) -> str:
    return ", ".join(f"{name}:{count}" for name, count in counter.most_common(limit))


def _sample_paths(paths: list[str], limit: int = 8) -> str:
    return " | ".join(paths[:limit])


def _format_bytes(value: int) -> str:
    sign = "-" if value < 0 else ""
    amount = abs(value)
    units = ["B", "KB", "MB", "GB"]
    numeric = float(amount)
    unit = units[0]
    for unit in units:
        if numeric < 1024 or unit == units[-1]:
            break
        numeric /= 1024
    if unit == "B":
        return f"{sign}{amount} B"
    return f"{sign}{numeric:.2f} {unit}"


def _read_text_file_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _resource_package_role(package: str, top_dirs: Counter[str], suffixes: Counter[str]) -> str:
    if package == "-1":
        return "base-or-streaming"
    total_files = max(1, sum(top_dirs.values()))
    audio_files = top_dirs.get("Audio", 0) + suffixes.get(".bnk", 0)
    video_files = top_dirs.get("Video", 0) + suffixes.get(".mp4", 0)
    if audio_files / total_files >= 0.4:
        return "audio"
    if video_files / total_files >= 0.4:
        return "video"
    top = {name for name, _count in top_dirs.most_common(3)}
    if "localization" in top:
        return "localization"
    if "texturenew" in top or "atlasnew" in top or "ui" in top:
        return "ui-texture"
    if "foundation" in top or "model" in top or "effect" in top:
        return "mixed-model-effect"
    return "resource-package"


def build_fanxiu_resource_package_report(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_asset_filelist_rows(root)
    streaming_paths = _read_streaming_file_set(root)
    package_stats: dict[str, dict[str, Any]] = {}
    file_rows: list[dict[str, object]] = []
    packages: defaultdict[str, list[str]] = defaultdict(list)

    for row in rows:
        package = str(row["package"])
        rel_path = str(row["path"])
        size = int(row["size"])
        top_dir = _path_top_dir(rel_path)
        suffix = _path_suffix(rel_path)
        apk_asset_path = root / "assets" / Path(rel_path)
        resource_path = resolved_resource_root / Path(rel_path)
        in_apk = apk_asset_path.is_file()
        in_resource = resource_path.is_file()
        is_streaming = rel_path in streaming_paths

        stat = package_stats.setdefault(
            package,
            {
                "package": package,
                "file_count": 0,
                "total_size": 0,
                "apk_present_count": 0,
                "resource_present_count": 0,
                "streaming_count": 0,
                "top_dirs": Counter(),
                "suffixes": Counter(),
                "samples": [],
            },
        )
        stat["file_count"] += 1
        stat["total_size"] += size
        stat["apk_present_count"] += 1 if in_apk else 0
        stat["resource_present_count"] += 1 if in_resource else 0
        stat["streaming_count"] += 1 if is_streaming else 0
        stat["top_dirs"][top_dir] += 1
        stat["suffixes"][suffix] += 1
        if len(stat["samples"]) < 10:
            stat["samples"].append(rel_path)
        packages[package].append(rel_path)

        file_rows.append(
            {
                "package": package,
                "role": "",
                "path": rel_path,
                "size": size,
                "md5": row["md5"],
                "top_dir": top_dir,
                "suffix": suffix,
                "in_apk": int(in_apk),
                "in_resource": int(in_resource),
                "is_streaming": int(is_streaming),
            }
        )

    package_rows: list[dict[str, object]] = []
    role_by_package: dict[str, str] = {}
    for stat in package_stats.values():
        role = _resource_package_role(stat["package"], stat["top_dirs"], stat["suffixes"])
        role_by_package[str(stat["package"])] = role
        package_rows.append(
            {
                "package": stat["package"],
                "role": role,
                "file_count": stat["file_count"],
                "total_size": stat["total_size"],
                "apk_present_count": stat["apk_present_count"],
                "resource_present_count": stat["resource_present_count"],
                "streaming_count": stat["streaming_count"],
                "top_dirs": _counter_preview(stat["top_dirs"]),
                "suffixes": _counter_preview(stat["suffixes"]),
                "samples": _sample_paths(stat["samples"]),
            }
        )
    package_rows.sort(key=lambda item: (str(item["package"]) == "-1", int(item["package"]) if str(item["package"]).lstrip("-").isdigit() else 999999))
    for file_row in file_rows:
        file_row["role"] = role_by_package.get(str(file_row["package"]), "")
    file_rows.sort(key=lambda item: (str(item["package"]), str(item["path"]).lower()))

    package_count = _write_tsv(
        output_dir / "resource_packages.tsv",
        [
            "package",
            "role",
            "file_count",
            "total_size",
            "apk_present_count",
            "resource_present_count",
            "streaming_count",
            "top_dirs",
            "suffixes",
            "samples",
        ],
        package_rows,
    )
    package_file_count = _write_tsv(
        output_dir / "resource_package_files.tsv",
        ["package", "role", "path", "size", "md5", "top_dir", "suffix", "in_apk", "in_resource", "is_streaming"],
        file_rows,
    )

    manifest_info = {
        "apk_root": str(root),
        "resource_root": str(resolved_resource_root),
        "filelist_version": _read_text_file_if_exists(root / "assets" / "filelistVersion"),
        "app_version": _read_text_file_if_exists(root / "assets" / "AppVersion.txt"),
        "version_txt": _read_text_file_if_exists(root / "assets" / "version.txt"),
        "filelist_rows": len(rows),
        "streaming_rows": len(streaming_paths),
    }
    markdown_lines = [
        "# 凡修资源包 packageId 报告",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 资源目录：`{resolved_resource_root}`",
        f"- filelistVersion：`{manifest_info['filelist_version']}`",
        f"- AppVersion：`{manifest_info['app_version']}`",
        f"- version.txt：`{manifest_info['version_txt']}`",
        "",
        "## Package 汇总",
        "",
    ]
    for row in package_rows:
        markdown_lines.append(
            f"- package `{row['package']}` ({row['role']}): {row['file_count']} files, "
            f"{row['total_size']} bytes, dirs={row['top_dirs']}"
        )
    (output_dir / "resource_package_report.md").write_text("\n".join(markdown_lines), encoding="utf-8")

    result = {
        "apk_root": str(root),
        "resource_root": str(resolved_resource_root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": manifest_info,
        "counts": {
            "packages": package_count,
            "package_files": package_file_count,
            "streaming_files": len(streaming_paths),
        },
        "outputs": {
            "summary": str(output_dir / "resource_package_report.json"),
            "markdown": str(output_dir / "resource_package_report.md"),
            "packages": str(output_dir / "resource_packages.tsv"),
            "files": str(output_dir / "resource_package_files.tsv"),
        },
    }
    (output_dir / "resource_package_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_fanxiu_taptap_download_dat_package_probe(
    *,
    download_path: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Inspect TapTap's local `*.dat` download package without extracting it."""
    path = Path(download_path or DEFAULT_TAPTAP_DOWNLOAD_DAT_PATH).resolve()
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        raise FanxiuResourceError(f"下载包不存在：{path}")

    with path.open("rb") as f:
        head = f.read(256)
    is_zip = zipfile.is_zipfile(path)

    entry_rows: list[dict[str, object]] = []
    top_dir_stats: dict[str, dict[str, Any]] = {}
    suffix_stats: dict[str, dict[str, Any]] = {}
    counts = {
        "entries": 0,
        "files": 0,
        "directories": 0,
        "total_uncompressed_bytes": 0,
        "total_compressed_bytes": 0,
        "vdi_count": 0,
        "filelist_csv_count": 0,
        "frxx_named_entry_count": 0,
    }
    largest: list[dict[str, object]] = []

    if is_zip:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            counts["entries"] = len(infos)
            for info in infos:
                name = info.filename.replace("\\", "/")
                cleaned_name = name.rstrip("/")
                parts = cleaned_name.split("/") if cleaned_name else [name]
                top_dir = parts[0] if parts and parts[0] else "<root>"
                suffix = Path(cleaned_name).suffix.lower() if cleaned_name else ""
                suffix = suffix or "<no_ext>"
                is_dir = info.is_dir()
                counts["directories" if is_dir else "files"] += 1
                counts["total_uncompressed_bytes"] += int(info.file_size)
                counts["total_compressed_bytes"] += int(info.compress_size)
                if suffix == ".vdi":
                    counts["vdi_count"] += 1
                if cleaned_name.lower().endswith("filelist.csv"):
                    counts["filelist_csv_count"] += 1
                if "frxx" in cleaned_name.lower():
                    counts["frxx_named_entry_count"] += 1

                top_stat = top_dir_stats.setdefault(
                    top_dir,
                    {
                        "name": top_dir,
                        "entry_count": 0,
                        "file_count": 0,
                        "directory_count": 0,
                        "uncompressed_bytes": 0,
                        "compressed_bytes": 0,
                        "samples": [],
                    },
                )
                top_stat["entry_count"] += 1
                top_stat["file_count"] += 0 if is_dir else 1
                top_stat["directory_count"] += 1 if is_dir else 0
                top_stat["uncompressed_bytes"] += int(info.file_size)
                top_stat["compressed_bytes"] += int(info.compress_size)
                if len(top_stat["samples"]) < 8 and cleaned_name:
                    top_stat["samples"].append(cleaned_name)

                suffix_stat = suffix_stats.setdefault(
                    suffix,
                    {
                        "suffix": suffix,
                        "file_count": 0,
                        "uncompressed_bytes": 0,
                        "compressed_bytes": 0,
                        "samples": [],
                    },
                )
                if not is_dir:
                    suffix_stat["file_count"] += 1
                    suffix_stat["uncompressed_bytes"] += int(info.file_size)
                    suffix_stat["compressed_bytes"] += int(info.compress_size)
                    if len(suffix_stat["samples"]) < 8 and cleaned_name:
                        suffix_stat["samples"].append(cleaned_name)

                if len(largest) < 30 or info.file_size > min(int(row["uncompressed_bytes"]) for row in largest):
                    largest.append(
                        {
                            "name": cleaned_name,
                            "top_dir": top_dir,
                            "suffix": suffix,
                            "uncompressed_bytes": int(info.file_size),
                            "compressed_bytes": int(info.compress_size),
                        }
                    )
                    largest.sort(key=lambda row: int(row["uncompressed_bytes"]), reverse=True)
                    largest = largest[:30]

                entry_rows.append(
                    {
                        "name": cleaned_name or name,
                        "top_dir": top_dir,
                        "suffix": suffix,
                        "is_dir": int(is_dir),
                        "uncompressed_bytes": int(info.file_size),
                        "compressed_bytes": int(info.compress_size),
                        "compression": int(info.compress_type),
                        "modified": "-".join(f"{part:02d}" for part in info.date_time[:3])
                        + " "
                        + ":".join(f"{part:02d}" for part in info.date_time[3:]),
                    }
                )

    top_dir_rows = [
        {
            "top_dir": name,
            "entry_count": stat["entry_count"],
            "file_count": stat["file_count"],
            "directory_count": stat["directory_count"],
            "uncompressed_bytes": stat["uncompressed_bytes"],
            "compressed_bytes": stat["compressed_bytes"],
            "samples": _sample_paths(stat["samples"]),
        }
        for name, stat in top_dir_stats.items()
    ]
    top_dir_rows.sort(key=lambda row: int(row["uncompressed_bytes"]), reverse=True)
    suffix_rows = [
        {
            "suffix": name,
            "file_count": stat["file_count"],
            "uncompressed_bytes": stat["uncompressed_bytes"],
            "compressed_bytes": stat["compressed_bytes"],
            "samples": _sample_paths(stat["samples"]),
        }
        for name, stat in suffix_stats.items()
    ]
    suffix_rows.sort(key=lambda row: int(row["uncompressed_bytes"]), reverse=True)
    entry_rows.sort(key=lambda row: (str(row["top_dir"]).lower(), str(row["name"]).lower()))

    entries_path = output_dir / "taptap_download_dat_entries.tsv"
    top_dirs_path = output_dir / "taptap_download_dat_top_dirs.tsv"
    suffixes_path = output_dir / "taptap_download_dat_suffixes.tsv"
    report_path = output_dir / "taptap_download_dat_package_report.md"
    json_path = output_dir / "taptap_download_dat_package_report.json"
    _write_tsv(
        entries_path,
        [
            "name",
            "top_dir",
            "suffix",
            "is_dir",
            "uncompressed_bytes",
            "compressed_bytes",
            "compression",
            "modified",
        ],
        entry_rows,
    )
    _write_tsv(top_dirs_path, ["top_dir", "entry_count", "file_count", "directory_count", "uncompressed_bytes", "compressed_bytes", "samples"], top_dir_rows)
    _write_tsv(suffixes_path, ["suffix", "file_count", "uncompressed_bytes", "compressed_bytes", "samples"], suffix_rows)

    top_dirs = {str(row["top_dir"]) for row in top_dir_rows}
    looks_like_mumu_package = bool({"hypervisor", "nx_device", "nx_main"} & top_dirs) and counts["vdi_count"] > 0
    verdict = {
        "is_zip_archive": is_zip,
        "looks_like_mumu_emulator_package": looks_like_mumu_package,
        "contains_vm_disk_images": counts["vdi_count"] > 0,
        "contains_filelist_csv": counts["filelist_csv_count"] > 0,
        "contains_frxx_named_entries": counts["frxx_named_entry_count"] > 0,
        "not_fanxiu_hot_update_resource_package": is_zip and looks_like_mumu_package and counts["filelist_csv_count"] == 0,
    }
    source = {
        "download_path": str(path),
        "size_bytes": path.stat().st_size,
        "head_hex": head.hex(" "),
        "head_ascii": "".join(chr(item) if 32 <= item < 127 else "." for item in head),
    }
    markdown_lines = [
        "# TapTap download dat package report",
        "",
        "Read-only central-directory inspection of the local TapTap/MuMu `*.dat` package. This does not extract or install anything.",
        "",
        "## Verdict",
        "",
    ]
    for key, value in verdict.items():
        markdown_lines.append(f"- `{key}`: `{value}`")
    markdown_lines.extend(
        [
            "",
            "## Stats",
            "",
            f"- path: `{path}`",
            f"- physical size: `{_format_bytes(path.stat().st_size)}`",
            f"- archive entries: `{counts['entries']}`",
            f"- files/directories: `{counts['files']}` / `{counts['directories']}`",
            f"- uncompressed size: `{_format_bytes(int(counts['total_uncompressed_bytes']))}`",
            f"- compressed size: `{_format_bytes(int(counts['total_compressed_bytes']))}`",
            f"- `.vdi` entries: `{counts['vdi_count']}`",
            "",
            "## Largest Entries",
            "",
        ]
    )
    for row in largest[:12]:
        markdown_lines.append(
            f"- `{row['name']}`: {_format_bytes(int(row['uncompressed_bytes']))} -> {_format_bytes(int(row['compressed_bytes']))}"
        )
    markdown_lines.extend(["", "## Top Directories", ""])
    for row in top_dir_rows[:12]:
        markdown_lines.append(
            f"- `{row['top_dir']}`: {row['file_count']} files, {_format_bytes(int(row['uncompressed_bytes']))}, samples={row['samples']}"
        )
    markdown_lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This package is the TapTap/MuMu emulator installer/update payload when `looks_like_mumu_emulator_package=true`. The game hot-update/resource payload for this analysis remains `frxx_game_files` plus its `filelist.csv`, not this `101000000.dat` archive.",
        ]
    )
    report_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")
    result = {
        "source": source,
        "counts": counts,
        "verdict": verdict,
        "samples": {
            "top_dirs": top_dir_rows[:20],
            "suffixes": suffix_rows[:20],
            "largest_entries": largest[:30],
        },
        "outputs": {
            "markdown": str(report_path),
            "json": str(json_path),
            "entries": str(entries_path),
            "top_dirs": str(top_dirs_path),
            "suffixes": str(suffixes_path),
        },
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


_RESOURCE_MANIFEST_DIFF_INTERESTING_TERMS = (
    "config",
    "lua",
    "gongfa",
    "faze",
    "skill",
    "resdownload",
    "atlasnew",
    "texturenew",
    "effect",
    "playable",
    "foundation",
    "item",
    "shop",
    "activity",
)

_RESOURCE_MANIFEST_DIFF_STATUS_ORDER = {
    "added": 0,
    "changed": 1,
    "removed": 2,
    "unchanged": 3,
}


def _filelist_by_path(rows: list[dict[str, object]]) -> tuple[dict[str, dict[str, object]], int]:
    result: dict[str, dict[str, object]] = {}
    duplicate_count = 0
    for row in rows:
        rel_path = str(row.get("path") or "")
        if rel_path in result:
            duplicate_count += 1
        result[rel_path] = row
    return result, duplicate_count


def _manifest_diff_changed_fields(apk_row: dict[str, object] | None, resource_row: dict[str, object] | None) -> tuple[str, str]:
    if apk_row is None:
        return "added", "new"
    if resource_row is None:
        return "removed", "missing"
    changed_fields: list[str] = []
    for field in ("size", "md5", "package"):
        if str(apk_row.get(field, "")) != str(resource_row.get(field, "")):
            changed_fields.append(field)
    if not changed_fields:
        return "unchanged", ""
    return "changed", ",".join(changed_fields)


def _manifest_diff_interesting_term(path: str) -> str:
    lower = path.lower()
    return next((term for term in _RESOURCE_MANIFEST_DIFF_INTERESTING_TERMS if term in lower), "")


def _manifest_diff_actual_rel_path(root: Path, rel_path: str, md5: str, *, apk_asset: bool) -> str:
    base = root / "assets" if apk_asset else root
    direct = base / Path(rel_path)
    if direct.is_file():
        return direct.relative_to(base).as_posix()
    if md5:
        rel = Path(rel_path)
        hashed = base / rel.parent / f"{rel.stem}_{md5}{rel.suffix}"
        if hashed.is_file():
            return hashed.relative_to(base).as_posix()
    return ""


def _manifest_diff_file_present(root: Path, rel_path: str, md5: str, *, apk_asset: bool) -> int:
    return int(bool(_manifest_diff_actual_rel_path(root, rel_path, md5, apk_asset=apk_asset)))


def _build_manifest_diff_summary_rows(rows: list[dict[str, object]], group_field: str) -> list[dict[str, object]]:
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        status = str(row["status"])
        if status == "unchanged":
            continue
        group = str(row.get(group_field) or "<blank>")
        stat = stats.setdefault(
            (status, group),
            {
                "status": status,
                group_field: group,
                "file_count": 0,
                "apk_size": 0,
                "resource_size": 0,
                "size_delta": 0,
                "top_dirs": Counter(),
                "suffixes": Counter(),
                "samples": [],
            },
        )
        stat["file_count"] += 1
        stat["apk_size"] += int(row.get("apk_size") or 0)
        stat["resource_size"] += int(row.get("resource_size") or 0)
        stat["size_delta"] += int(row.get("size_delta") or 0)
        stat["top_dirs"][str(row.get("top_dir") or ".")] += 1
        stat["suffixes"][str(row.get("suffix") or "<none>")] += 1
        if len(stat["samples"]) < 10:
            stat["samples"].append(str(row.get("path") or ""))

    summary_rows: list[dict[str, object]] = []
    for stat in stats.values():
        summary_rows.append(
            {
                "status": stat["status"],
                group_field: stat[group_field],
                "file_count": stat["file_count"],
                "apk_size": stat["apk_size"],
                "resource_size": stat["resource_size"],
                "size_delta": stat["size_delta"],
                "top_dirs": _counter_preview(stat["top_dirs"]),
                "suffixes": _counter_preview(stat["suffixes"]),
                "samples": _sample_paths(stat["samples"], limit=6),
            }
        )
    summary_rows.sort(
        key=lambda item: (
            _RESOURCE_MANIFEST_DIFF_STATUS_ORDER.get(str(item["status"]), 99),
            -int(item["resource_size"] or 0),
            -int(item["file_count"] or 0),
            str(item.get(group_field) or ""),
        )
    )
    return summary_rows


def _write_resource_manifest_diff_markdown(
    path: Path,
    *,
    root: Path,
    resource_root: Path,
    output_dir: Path,
    result: dict[str, Any],
    top_dir_rows: list[dict[str, object]],
    package_rows: list[dict[str, object]],
    diff_rows: list[dict[str, object]],
) -> None:
    counts = result["counts"]
    manifest = result["manifest"]
    changed_rows = [row for row in diff_rows if row["status"] != "unchanged"]
    interesting_rows = [
        row
        for row in changed_rows
        if row.get("interesting_term")
    ][:40]
    sample_rows = interesting_rows or changed_rows[:40]
    lines = [
        "# 凡修资源清单差异报告",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 下载资源目录：`{resource_root}`",
        f"- 索引目录：`{output_dir}`",
        f"- APK filelistVersion：`{manifest['apk_filelist_version']}`",
        f"- 下载资源 filelistVersion：`{manifest['resource_filelist_version']}`",
        f"- APK 清单行数：{manifest['apk_filelist_rows']}；下载资源清单行数：{manifest['resource_filelist_rows']}",
        f"- 估算本次下载/更新涉及资源体积：{_format_bytes(counts['update_candidate_bytes'])}",
        "",
        "## 状态汇总",
        "",
        "| 状态 | 文件数 | APK 字节 | 下载资源字节 | 差值 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for status in ("added", "changed", "removed", "unchanged"):
        status_counts = counts["by_status"].get(status, {})
        lines.append(
            "| "
            f"{status} | "
            f"{status_counts.get('file_count', 0)} | "
            f"{_format_bytes(int(status_counts.get('apk_size', 0)))} | "
            f"{_format_bytes(int(status_counts.get('resource_size', 0)))} | "
            f"{_format_bytes(int(status_counts.get('size_delta', 0)))} |"
        )

    lines.extend(["", "## 目录差异 Top", "", "| 状态 | 目录 | 文件数 | 下载资源字节 | 差值 | 样例 |", "| --- | --- | ---: | ---: | ---: | --- |"])
    for row in top_dir_rows[:30]:
        lines.append(
            "| "
            f"{row['status']} | "
            f"{_markdown_table_cell(row['top_dir'])} | "
            f"{row['file_count']} | "
            f"{_format_bytes(int(row['resource_size']))} | "
            f"{_format_bytes(int(row['size_delta']))} | "
            f"{_markdown_table_cell(row['samples'], limit=180)} |"
        )

    lines.extend(["", "## Package 差异 Top", "", "| 状态 | package | 文件数 | 下载资源字节 | 差值 | 目录 |", "| --- | --- | ---: | ---: | ---: | --- |"])
    for row in package_rows[:30]:
        lines.append(
            "| "
            f"{row['status']} | "
            f"{_markdown_table_cell(row['package'])} | "
            f"{row['file_count']} | "
            f"{_format_bytes(int(row['resource_size']))} | "
            f"{_format_bytes(int(row['size_delta']))} | "
            f"{_markdown_table_cell(row['top_dirs'], limit=180)} |"
        )

    lines.extend(
        [
            "",
            "## 关键样例",
            "",
            "| 状态 | 路径 | 变化字段 | APK | 下载资源 | package |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sample_rows[:40]:
        apk_desc = f"{_format_bytes(int(row['apk_size']))}; {row['apk_md5']}"
        resource_desc = f"{_format_bytes(int(row['resource_size']))}; {row['resource_md5']}"
        package_desc = f"{row['apk_package']} -> {row['resource_package']}"
        lines.append(
            "| "
            f"{row['status']} | "
            f"{_markdown_table_cell(row['path'], limit=220)} | "
            f"{_markdown_table_cell(row['changed_fields'])} | "
            f"{_markdown_table_cell(apk_desc, limit=120)} | "
            f"{_markdown_table_cell(resource_desc, limit=120)} | "
            f"{_markdown_table_cell(package_desc, limit=80)} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_resource_manifest_diff_report(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    apk_filelist_path = root / "assets" / "filelist.csv"
    resource_filelist_path = resolved_resource_root / "filelist.csv"
    apk_rows = _read_filelist_rows(apk_filelist_path)
    resource_rows = _read_filelist_rows(resource_filelist_path)
    if not apk_rows:
        raise FanxiuResourceError(f"APK 内置资源清单为空或不存在：{apk_filelist_path}")
    if not resource_rows:
        raise FanxiuResourceError(f"下载资源清单为空或不存在：{resource_filelist_path}")

    apk_by_path, apk_duplicate_count = _filelist_by_path(apk_rows)
    resource_by_path, resource_duplicate_count = _filelist_by_path(resource_rows)
    all_paths = sorted(set(apk_by_path) | set(resource_by_path), key=str.lower)
    diff_rows: list[dict[str, object]] = []
    status_stats: dict[str, dict[str, int]] = {
        status: {"file_count": 0, "apk_size": 0, "resource_size": 0, "size_delta": 0}
        for status in _RESOURCE_MANIFEST_DIFF_STATUS_ORDER
    }

    for rel_path in all_paths:
        apk_row = apk_by_path.get(rel_path)
        resource_row = resource_by_path.get(rel_path)
        status, changed_fields = _manifest_diff_changed_fields(apk_row, resource_row)
        apk_size = int(apk_row.get("size") or 0) if apk_row else 0
        resource_size = int(resource_row.get("size") or 0) if resource_row else 0
        apk_md5 = str(apk_row.get("md5") or "") if apk_row else ""
        resource_md5 = str(resource_row.get("md5") or "") if resource_row else ""
        apk_package = str(apk_row.get("package") or "") if apk_row else ""
        resource_package = str(resource_row.get("package") or "") if resource_row else ""
        package = resource_package or apk_package or "<blank>"
        apk_actual_path = _manifest_diff_actual_rel_path(root, rel_path, apk_md5, apk_asset=True)
        resource_actual_path = _manifest_diff_actual_rel_path(resolved_resource_root, rel_path, resource_md5, apk_asset=False)
        row = {
            "status": status,
            "path": rel_path,
            "changed_fields": changed_fields,
            "apk_size": apk_size,
            "resource_size": resource_size,
            "size_delta": resource_size - apk_size,
            "apk_md5": apk_md5,
            "resource_md5": resource_md5,
            "apk_package": apk_package,
            "resource_package": resource_package,
            "package": package,
            "top_dir": _path_top_dir(rel_path),
            "suffix": _path_suffix(rel_path),
            "interesting_term": _manifest_diff_interesting_term(rel_path),
            "apk_present": int(bool(apk_actual_path)),
            "resource_present": int(bool(resource_actual_path)),
            "apk_actual_path": apk_actual_path,
            "resource_actual_path": resource_actual_path,
        }
        diff_rows.append(row)
        stat = status_stats[status]
        stat["file_count"] += 1
        stat["apk_size"] += apk_size
        stat["resource_size"] += resource_size
        stat["size_delta"] += resource_size - apk_size

    diff_rows.sort(
        key=lambda row: (
            _RESOURCE_MANIFEST_DIFF_STATUS_ORDER.get(str(row["status"]), 99),
            0 if row.get("interesting_term") else 1,
            str(row["top_dir"]).lower(),
            str(row["path"]).lower(),
        )
    )
    top_dir_rows = _build_manifest_diff_summary_rows(diff_rows, "top_dir")
    package_rows = _build_manifest_diff_summary_rows(diff_rows, "package")

    fields = [
        "status",
        "path",
        "changed_fields",
        "apk_size",
        "resource_size",
        "size_delta",
        "apk_md5",
        "resource_md5",
        "apk_package",
        "resource_package",
        "package",
        "top_dir",
        "suffix",
        "interesting_term",
        "apk_present",
        "resource_present",
        "apk_actual_path",
        "resource_actual_path",
    ]
    diff_row_count = _write_tsv(output_dir / "resource_manifest_diff.tsv", fields, diff_rows)
    top_dir_row_count = _write_tsv(
        output_dir / "resource_manifest_diff_by_top_dir.tsv",
        ["status", "top_dir", "file_count", "apk_size", "resource_size", "size_delta", "top_dirs", "suffixes", "samples"],
        top_dir_rows,
    )
    package_row_count = _write_tsv(
        output_dir / "resource_manifest_diff_by_package.tsv",
        ["status", "package", "file_count", "apk_size", "resource_size", "size_delta", "top_dirs", "suffixes", "samples"],
        package_rows,
    )

    update_candidate_bytes = status_stats["added"]["resource_size"] + status_stats["changed"]["resource_size"]
    result = {
        "apk_root": str(root),
        "resource_root": str(resolved_resource_root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest": {
            "apk_filelist": str(apk_filelist_path),
            "resource_filelist": str(resource_filelist_path),
            "apk_filelist_version": _read_text_file_if_exists(root / "assets" / "filelistVersion"),
            "resource_filelist_version": _read_text_file_if_exists(resolved_resource_root / "filelistVersion"),
            "apk_filelist_rows": len(apk_rows),
            "resource_filelist_rows": len(resource_rows),
            "apk_duplicate_paths": apk_duplicate_count,
            "resource_duplicate_paths": resource_duplicate_count,
        },
        "counts": {
            "diff_rows": diff_row_count,
            "top_dir_rows": top_dir_row_count,
            "package_rows": package_row_count,
            "update_candidate_bytes": update_candidate_bytes,
            "by_status": status_stats,
        },
        "outputs": {
            "summary": str(output_dir / "resource_manifest_diff_report.json"),
            "markdown": str(output_dir / "resource_manifest_diff_report.md"),
            "diff": str(output_dir / "resource_manifest_diff.tsv"),
            "by_top_dir": str(output_dir / "resource_manifest_diff_by_top_dir.tsv"),
            "by_package": str(output_dir / "resource_manifest_diff_by_package.tsv"),
        },
    }
    _write_resource_manifest_diff_markdown(
        output_dir / "resource_manifest_diff_report.md",
        root=root,
        resource_root=resolved_resource_root,
        output_dir=output_dir,
        result=result,
        top_dir_rows=top_dir_rows,
        package_rows=package_rows,
        diff_rows=diff_rows,
    )
    (output_dir / "resource_manifest_diff_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _snippet(text: str, start: int, length: int, radius: int = 120) -> str:
    left = max(0, start - radius)
    right = min(len(text), start + length + radius)
    return text[left:right]


def _looks_scannable(path: Path, size: int) -> bool:
    if size <= 0 or size > 16 * 1024 * 1024:
        return False
    suffix = path.suffix.lower()
    if suffix in _BINARY_SCAN_SKIP_SUFFIXES:
        return False
    return True


def _write_asset_keyword_hits(root: Path, output_dir: Path, keywords: tuple[str, ...], limit: int) -> int:
    fields = ["relative_path", "keyword", "snippet"]
    count = 0
    output_path = output_dir / "asset_keyword_hits.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for path in _iter_apk_files(root):
            if count >= limit:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not _looks_scannable(path, size):
                continue
            try:
                text = path.read_bytes().decode("utf-8", errors="ignore")
            except OSError:
                continue
            if not text:
                continue
            normalized = text.lower()
            rel = path.relative_to(root).as_posix()
            for keyword in keywords:
                if count >= limit:
                    break
                needle = keyword.lower()
                pos = normalized.find(needle)
                per_file_keyword_hits = 0
                while pos >= 0 and per_file_keyword_hits < 5 and count < limit:
                    writer.writerow(
                        {
                            "relative_path": _clean_tsv_cell(rel),
                            "keyword": _clean_tsv_cell(keyword),
                            "snippet": _clean_tsv_cell(_snippet(text, pos, len(keyword))),
                        }
                    )
                    count += 1
                    per_file_keyword_hits += 1
                    pos = normalized.find(needle, pos + len(needle))
    return count


def _write_dex_indexes(root: Path, output_dir: Path, keywords: tuple[str, ...], keyword_hit_limit: int) -> dict[str, Any]:
    dex_files = sorted(root.glob("classes*.dex"), key=lambda item: (len(item.name), item.name))
    summaries: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    keyword_seen: set[tuple[str, str, str, str]] = set()
    keyword_hit_count = 0

    string_fields = ["dex", "index", "value"]
    class_fields = ["dex", "index", "descriptor", "java_name", "package", "short_name"]
    method_fields = ["dex", "index", "class_descriptor", "class_name", "name", "qualified_name"]
    hit_fields = ["kind", "source", "keyword", "value"]

    with (
        (output_dir / "dex_strings.tsv").open("w", encoding="utf-8", newline="") as strings_f,
        (output_dir / "dex_classes.tsv").open("w", encoding="utf-8", newline="") as classes_f,
        (output_dir / "dex_methods.tsv").open("w", encoding="utf-8", newline="") as methods_f,
        (output_dir / "dex_keyword_hits.tsv").open("w", encoding="utf-8", newline="") as hits_f,
    ):
        strings_writer = csv.DictWriter(strings_f, fieldnames=string_fields, delimiter="\t", lineterminator="\n")
        classes_writer = csv.DictWriter(classes_f, fieldnames=class_fields, delimiter="\t", lineterminator="\n")
        methods_writer = csv.DictWriter(methods_f, fieldnames=method_fields, delimiter="\t", lineterminator="\n")
        hits_writer = csv.DictWriter(hits_f, fieldnames=hit_fields, delimiter="\t", lineterminator="\n")
        strings_writer.writeheader()
        classes_writer.writeheader()
        methods_writer.writeheader()
        hits_writer.writeheader()

        for dex_path in dex_files:
            try:
                parsed = _parse_dex(dex_path)
            except FanxiuResourceError as exc:
                errors.append({"dex": dex_path.name, "error": str(exc)})
                continue
            summaries.append(parsed["summary"])
            source = parsed["name"]

            for index, value in enumerate(parsed["strings"]):
                strings_writer.writerow(
                    {
                        "dex": source,
                        "index": index,
                        "value": _clean_tsv_cell(value),
                    }
                )
                if keyword_hit_count < keyword_hit_limit:
                    for hit in _value_keyword_hits(kind="string", source=source, value=value, keywords=keywords, seen=keyword_seen):
                        hits_writer.writerow({field: _clean_tsv_cell(hit.get(field, "")) for field in hit_fields})
                        keyword_hit_count += 1
                        if keyword_hit_count >= keyword_hit_limit:
                            break

            for index, descriptor in enumerate(parsed["classes"]):
                java_name = _descriptor_to_java_name(descriptor)
                package, _, short_name = java_name.rpartition(".")
                classes_writer.writerow(
                    {
                        "dex": source,
                        "index": index,
                        "descriptor": _clean_tsv_cell(descriptor),
                        "java_name": _clean_tsv_cell(java_name),
                        "package": _clean_tsv_cell(package),
                        "short_name": _clean_tsv_cell(short_name or java_name),
                    }
                )
                if keyword_hit_count < keyword_hit_limit:
                    for hit in _value_keyword_hits(kind="class", source=source, value=java_name, keywords=keywords, seen=keyword_seen):
                        hits_writer.writerow({field: _clean_tsv_cell(hit.get(field, "")) for field in hit_fields})
                        keyword_hit_count += 1
                        if keyword_hit_count >= keyword_hit_limit:
                            break

            for method in parsed["methods"]:
                method_row = {"dex": source, **method}
                methods_writer.writerow({field: _clean_tsv_cell(method_row.get(field, "")) for field in method_fields})
                if keyword_hit_count < keyword_hit_limit:
                    qualified_name = str(method.get("qualified_name") or "")
                    for hit in _value_keyword_hits(kind="method", source=source, value=qualified_name, keywords=keywords, seen=keyword_seen):
                        hits_writer.writerow({field: _clean_tsv_cell(hit.get(field, "")) for field in hit_fields})
                        keyword_hit_count += 1
                        if keyword_hit_count >= keyword_hit_limit:
                            break

    return {
        "files": summaries,
        "errors": errors,
        "keyword_hit_count": keyword_hit_count,
    }


def _runtime_scan_file_allowed(path: Path, size: int) -> bool:
    if size <= 0 or size > 16 * 1024 * 1024:
        return False
    suffix = path.suffix.lower()
    if suffix in _BINARY_SCAN_SKIP_SUFFIXES:
        return False
    return True


def _runtime_snippet(text: str, needle: str, *, radius: int = 180) -> str:
    pos = text.lower().find(needle.lower())
    if pos < 0:
        return text[: radius * 2]
    left = max(0, pos - radius)
    right = min(len(text), pos + len(needle) + radius)
    return text[left:right]


def _runtime_url_interesting(url: str) -> bool:
    lower = url.lower()
    return any(term in lower for term in _APK_RUNTIME_URL_TERMS)


def _runtime_url_name(url: str) -> str:
    cleaned = url.replace("\\", "/")
    match = re.match(r"https?://([^/?#]+)(/[^?#]*)?", cleaned, flags=re.IGNORECASE)
    if not match:
        return "url"
    host = match.group(1)
    path = (match.group(2) or "").strip("/")
    if not path:
        return host
    head = "/".join(part for part in path.split("/")[:3] if part)
    return f"{host}/{head}" if head else host


def _add_runtime_candidate(
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
    *,
    category: str,
    confidence: int,
    source: str,
    keyword: str = "",
    name: str = "",
    value: str = "",
    note: str = "",
) -> None:
    if category_counts[category] >= _APK_RUNTIME_CATEGORY_LIMITS.get(category, 80):
        return
    key = (category, source, name, value)
    if key in seen:
        return
    seen.add(key)
    category_counts[category] += 1
    rows.append(
        {
            "category": category,
            "confidence": confidence,
            "source": source,
            "keyword": keyword,
            "name": name,
            "value": value,
            "note": note,
        }
    )


def _add_runtime_url_candidates(
    root: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    for path in _iter_apk_files(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if not _runtime_scan_file_allowed(path, size):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for match in _URL_BYTES_RE.finditer(data):
            url = match.group(0).decode("ascii", errors="ignore").rstrip(".,;)]}")
            if not url or not _runtime_url_interesting(url):
                continue
            lower = url.lower()
            confidence = 95 if any(term in lower for term in ("frxx", "akbing", "eyugame", "xiuxian")) else 75
            _add_runtime_candidate(
                rows,
                seen,
                category_counts,
                category="config_url",
                confidence=confidence,
                source=rel,
                keyword="http",
                name=_runtime_url_name(url),
                value=url,
                note="APK 内静态 URL 字符串，优先用于定位配置、公告、登录、资源下载入口。",
            )


def _add_runtime_symbol_candidates(
    root: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    for path in _iter_apk_files(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if not _runtime_scan_file_allowed(path, size):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        strings = [match.group(0).decode("ascii", errors="ignore") for match in _PRINTABLE_BYTES_RE.finditer(data)]
        if not strings:
            continue
        for category, needle, note in _APK_RUNTIME_SYMBOL_NEEDLES:
            needle_lower = needle.lower()
            for text in strings:
                if needle_lower not in text.lower():
                    continue
                _add_runtime_candidate(
                    rows,
                    seen,
                    category_counts,
                    category=category,
                    confidence=90 if category == "unity_boot_scene" else 85,
                    source=rel,
                    keyword=needle,
                    name=needle,
                    value=_runtime_snippet(text, needle),
                    note=note,
                )
                break


def _add_runtime_native_candidates(
    output_dir: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    for row in _read_tsv_rows(output_dir / "native_libs.tsv"):
        role = row.get("role", "")
        if role not in {"unity-il2cpp", "unity-runtime", "lua-bridge"}:
            continue
        _add_runtime_candidate(
            rows,
            seen,
            category_counts,
            category="native_bridge",
            confidence=80 if role == "lua-bridge" else 75,
            source=row.get("relative_path", ""),
            keyword=role,
            name=row.get("name", ""),
            value=row.get("abi", ""),
            note="原生库角色，用来判断 Unity/IL2CPP/Lua 桥接边界。",
        )


def _add_runtime_dex_candidates(
    output_dir: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    for row in _read_tsv_rows(output_dir / "dex_keyword_hits.tsv"):
        value = row.get("value", "")
        lower = value.lower()
        if not any(term in lower for term in ("com.sy.frxx", "unityplayeractivity", "unityplayer", "loadlibrary", "libil2cpp", "libtolua")):
            continue
        _add_runtime_candidate(
            rows,
            seen,
            category_counts,
            category="android_shell",
            confidence=65,
            source=row.get("source", ""),
            keyword=row.get("keyword", ""),
            name=row.get("kind", ""),
            value=value,
            note="DEX 层壳/SDK/启动相关符号；通常不等于核心玩法逻辑。",
        )


def _add_runtime_il2cpp_candidates(
    output_dir: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    interesting = (
        "gameres",
        "luabridge",
        "assetbundle",
        "httpdownload",
        "httploader",
        "coroutinehttp",
        "encryptstream",
        "tolua",
    )
    noisy_prefixes = (
        "system.",
        "mono.",
        "microsoft.",
        "ms.internal.",
        "unityengine.",
    )
    for row in _read_tsv_rows(output_dir / "il2cpp_keyword_hits.tsv"):
        value = row.get("value", "")
        lower = value.lower()
        if lower.startswith(noisy_prefixes):
            continue
        if not any(term in lower for term in interesting):
            continue
        _add_runtime_candidate(
            rows,
            seen,
            category_counts,
            category="il2cpp_symbol",
            confidence=70,
            source=f"global-metadata.dat:{row.get('kind', '')}:{row.get('index', '')}",
            keyword=row.get("keyword", ""),
            name=row.get("kind", ""),
            value=value,
            note="IL2CPP metadata 中的符号名；只能证明有名字，不能直接还原方法体。",
        )


def _add_runtime_asset_path_candidates(
    output_dir: Path,
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    category_counts: Counter[str],
) -> None:
    for row in _read_tsv_rows(output_dir / "asset_filelist.tsv"):
        path = row.get("path", "")
        lower = path.lower()
        matched = next((term for term in _APK_RUNTIME_ASSET_PATH_TERMS if term in lower), "")
        if not matched:
            continue
        confidence = 70 if matched in {"resdownload", "gongfa", "faze", "config"} else 55
        _add_runtime_candidate(
            rows,
            seen,
            category_counts,
            category="asset_path",
            confidence=confidence,
            source=row.get("kind", ""),
            keyword=matched,
            name=path,
            value=f"size={row.get('size', '')}; package={row.get('package', '')}; md5={row.get('md5', '')}",
            note="资源清单路径，可用于回查下载包、图鉴资源、UI 图集或特效资源。",
        )


def _markdown_table_cell(value: object, *, limit: int = 260) -> str:
    text = _clean_tsv_cell(value, limit=limit)
    text = text.replace("|", "\\|")
    return text


def _write_runtime_entry_markdown(path: Path, *, root: Path, output_dir: Path, rows: list[dict[str, object]]) -> None:
    by_category: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_category[str(row["category"])].append(row)

    lines = [
        "# 凡修 APK 运行入口候选报告",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：这是静态字符串和清单层面的入口候选，只负责指路；它不能直接还原 IL2CPP 方法体或运行时调用栈。",
        "",
        "## 优先观察",
        "",
    ]
    for row in rows[:20]:
        lines.append(
            f"- [{row['category']}] {row.get('name') or row.get('keyword')} "
            f"`{row.get('source', '')}`：{_markdown_table_cell(row.get('value', ''), limit=180)}"
        )

    lines.extend(["", "## 分类明细", ""])
    for category in sorted(by_category, key=lambda item: _APK_RUNTIME_CATEGORY_ORDER.get(item, 999)):
        category_rows = by_category[category]
        lines.extend(
            [
                f"### {category}",
                "",
                "| 置信度 | 来源 | 名称 | 值 | 备注 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in category_rows[:80]:
            lines.append(
                "| "
                f"{row.get('confidence', '')} | "
                f"{_markdown_table_cell(row.get('source', ''))} | "
                f"{_markdown_table_cell(row.get('name') or row.get('keyword'))} | "
                f"{_markdown_table_cell(row.get('value', ''))} | "
                f"{_markdown_table_cell(row.get('note', ''), limit=180)} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _read_printable_strings(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    return [match.group(0).decode("ascii", errors="ignore") for match in _PRINTABLE_BYTES_RE.finditer(data)]


def _read_axml_length8(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise FanxiuResourceError("AXML UTF-8 length 越界")
    first = data[offset]
    offset += 1
    if first & 0x80:
        if offset >= len(data):
            raise FanxiuResourceError("AXML UTF-8 extended length 越界")
        second = data[offset]
        offset += 1
        return ((first & 0x7F) << 8) | second, offset
    return first, offset


def _read_axml_length16(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise FanxiuResourceError("AXML UTF-16 length 越界")
    first = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    if first & 0x8000:
        if offset + 2 > len(data):
            raise FanxiuResourceError("AXML UTF-16 extended length 越界")
        second = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return ((first & 0x7FFF) << 16) | second, offset
    return first, offset


def _axml_string(strings: list[str], index: int, default: str = "") -> str:
    if 0 <= index < len(strings):
        return strings[index]
    return default


def _parse_axml_string_pool(data: bytes, offset: int) -> tuple[list[str], int]:
    if offset + 28 > len(data):
        raise FanxiuResourceError("AXML string pool 头部越界")
    chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
    if chunk_type != _AXML_STRING_POOL_TYPE:
        raise FanxiuResourceError(f"AXML 缺少 string pool：0x{chunk_type:04x}")
    if header_size < 28 or chunk_size < header_size or offset + chunk_size > len(data):
        raise FanxiuResourceError("AXML string pool 尺寸异常")

    string_count, _style_count, flags, strings_start, _styles_start = struct.unpack_from("<IIIII", data, offset + 8)
    offsets_start = offset + header_size
    if offsets_start + string_count * 4 > offset + chunk_size:
        raise FanxiuResourceError("AXML string offsets 越界")

    utf8 = bool(flags & _AXML_UTF8_FLAG)
    strings: list[str] = []
    for index in range(string_count):
        string_offset = struct.unpack_from("<I", data, offsets_start + index * 4)[0]
        cursor = offset + strings_start + string_offset
        if not (offset <= cursor < offset + chunk_size):
            strings.append("")
            continue
        if utf8:
            _utf16_size, cursor = _read_axml_length8(data, cursor)
            byte_size, cursor = _read_axml_length8(data, cursor)
            end = min(cursor + byte_size, offset + chunk_size)
            strings.append(data[cursor:end].decode("utf-8", errors="replace"))
        else:
            char_size, cursor = _read_axml_length16(data, cursor)
            end = min(cursor + char_size * 2, offset + chunk_size)
            strings.append(data[cursor:end].decode("utf-16le", errors="replace"))
    return strings, chunk_size


def _decode_axml_typed_value(strings: list[str], raw_index: int, data_type: int, data_value: int) -> str:
    if raw_index != _AXML_NO_INDEX:
        return _axml_string(strings, raw_index, f"@string/{raw_index}")
    if data_type == 0x03:
        return _axml_string(strings, data_value, f"@string/{data_value}")
    if data_type == 0x12:
        return "true" if data_value else "false"
    if data_type == 0x10:
        return str(data_value)
    if data_type == 0x11:
        return f"0x{data_value:08x}"
    if data_type == 0x01:
        return f"@0x{data_value:08x}"
    if 0x1C <= data_type <= 0x1F:
        return f"#{data_value:08x}"
    return f"0x{data_value:08x}"


def _normalize_manifest_attr_name(name: str) -> str:
    return name.split(":", 1)[-1].strip()


def _parse_binary_axml_manifest(data: bytes) -> dict[str, object]:
    if len(data) < 16:
        raise FanxiuResourceError("AXML 文件过短")
    xml_type, _xml_header_size, xml_size = struct.unpack_from("<HHI", data, 0)
    if xml_type != _AXML_XML_TYPE:
        raise FanxiuResourceError(f"不是 Android 二进制 XML：0x{xml_type:04x}")
    if xml_size and xml_size > len(data):
        raise FanxiuResourceError("AXML 文件尺寸字段越界")

    strings, string_pool_size = _parse_axml_string_pool(data, 8)
    cursor = 8 + string_pool_size
    events: list[dict[str, object]] = []
    stack: list[dict[str, object]] = []

    while cursor + 8 <= len(data):
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, cursor)
        if chunk_size < 8 or cursor + chunk_size > len(data):
            break
        if chunk_type == _AXML_START_ELEMENT_TYPE and header_size >= 16 and cursor + 36 <= len(data):
            line_number, _comment = struct.unpack_from("<II", data, cursor + 8)
            _namespace_idx, name_idx = struct.unpack_from("<II", data, cursor + 16)
            attr_start, attr_size, attr_count, _id_index, _class_index, _style_index = struct.unpack_from(
                "<HHHHHH",
                data,
                cursor + 24,
            )
            tag = _axml_string(strings, name_idx, f"tag_{name_idx}")
            attrs: dict[str, str] = {}
            attr_base = cursor + 16 + attr_start
            for attr_index in range(attr_count):
                attr_offset = attr_base + attr_index * attr_size
                if attr_offset + 20 > cursor + chunk_size:
                    continue
                _attr_ns, attr_name_idx, raw_value_idx = struct.unpack_from("<III", data, attr_offset)
                _value_size, _value_res0, data_type, data_value = struct.unpack_from("<HBBI", data, attr_offset + 12)
                attr_name = _normalize_manifest_attr_name(_axml_string(strings, attr_name_idx, f"attr_{attr_name_idx}"))
                attrs[attr_name] = _decode_axml_typed_value(strings, raw_value_idx, data_type, data_value)

            parent_component = next(
                (item for item in reversed(stack) if str(item.get("tag", "")) in _MANIFEST_COMPONENT_TAGS),
                None,
            )
            event: dict[str, object] = {
                "event": "start",
                "tag": tag,
                "attrs": attrs,
                "line": line_number,
                "depth": len(stack),
            }
            if parent_component is not None:
                event["parent_component_type"] = str(parent_component.get("tag", ""))
                event["parent_component_name"] = str(parent_component.get("attrs", {}).get("name", ""))
            events.append(event)
            stack.append(event)
        elif chunk_type == _AXML_END_ELEMENT_TYPE and cursor + 24 <= len(data):
            _line_number, _comment = struct.unpack_from("<II", data, cursor + 8)
            _namespace_idx, name_idx = struct.unpack_from("<II", data, cursor + 16)
            end_tag = _axml_string(strings, name_idx, "")
            while stack:
                item = stack.pop()
                if item.get("tag") == end_tag:
                    break
        cursor += chunk_size

    return {"format": "binary-axml", "strings": strings, "events": events, "errors": []}


def _parse_text_manifest(data: bytes) -> dict[str, object]:
    text = data.decode("utf-8", errors="ignore")
    if "<" not in text:
        text = data.decode("utf-16le", errors="ignore")
    attr_re = re.compile(r"([A-Za-z_][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
    tag_re = re.compile(r"<\s*(/)?\s*([A-Za-z_][\w:.-]*)([^<>]*)>")
    events: list[dict[str, object]] = []
    strings: list[str] = []
    stack: list[dict[str, object]] = []
    for match in tag_re.finditer(text):
        is_end, raw_tag, raw_attrs = match.groups()
        tag = raw_tag.split(":", 1)[-1]
        if tag.startswith(("?", "!")):
            continue
        if is_end:
            while stack:
                item = stack.pop()
                if item.get("tag") == tag:
                    break
            continue
        attrs = {
            _normalize_manifest_attr_name(name): value1 or value2 or ""
            for name, value1, value2 in attr_re.findall(raw_attrs)
        }
        parent_component = next(
            (item for item in reversed(stack) if str(item.get("tag", "")) in _MANIFEST_COMPONENT_TAGS),
            None,
        )
        event: dict[str, object] = {"event": "start", "tag": tag, "attrs": attrs, "line": "", "depth": len(stack)}
        if parent_component is not None:
            event["parent_component_type"] = str(parent_component.get("tag", ""))
            event["parent_component_name"] = str(parent_component.get("attrs", {}).get("name", ""))
        events.append(event)
        strings.extend([tag, *attrs.keys(), *attrs.values()])
        if not raw_attrs.strip().endswith("/"):
            stack.append(event)
    return {"format": "text-xml", "strings": list(dict.fromkeys(strings)), "events": events, "errors": []}


def _parse_android_manifest(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    errors: list[str] = []
    try:
        parsed = _parse_binary_axml_manifest(data)
        if parsed.get("events"):
            return parsed
        errors.extend(str(item) for item in parsed.get("errors", []))
    except (FanxiuResourceError, struct.error, UnicodeDecodeError) as exc:
        errors.append(str(exc))

    parsed = _parse_text_manifest(data)
    if parsed.get("events"):
        parsed["errors"] = errors
        return parsed

    strings = [match.group(0).decode("ascii", errors="ignore") for match in _PRINTABLE_BYTES_RE.finditer(data)]
    return {"format": "printable-fallback", "strings": list(dict.fromkeys(strings)), "events": [], "errors": errors}


def _manifest_string_category(value: str) -> str:
    text = value.strip()
    lower = text.lower()
    if not text:
        return "blank"
    if text.startswith("android.permission.") or ".permission." in lower or lower.endswith(".permission"):
        return "permission"
    if text.startswith("android.intent.action.") or lower.endswith(".action"):
        return "intent_action"
    if text.startswith("android.intent.category."):
        return "intent_category"
    if "http://" in lower or "https://" in lower or re.search(r"\b[a-z0-9.-]+\.[a-z]{2,}\b", lower):
        return "url_or_domain"
    if any(term in lower for term in ("frxx", "sy.frxx", "mobi37", "sqwan")):
        return "fanxiu_package"
    if any(term in lower for term in ("unity", "il2cpp", "flameunity")):
        return "unity"
    if any(term in lower for term in ("huawei", "honor", "getui", "igexin", "xiaomi", "vivo", "oppo", "meizu")):
        return "push_sdk"
    if any(term in lower for term in ("bytedance", "pangle", "openadsdk", "alipay", "unionpay", "aliyun")):
        return "third_party_sdk"
    if text.startswith(("com.", "cn.", "org.")):
        return "java_or_package"
    if lower in {"usescleartexttraffic", "networksecurityconfig", "allowbackup", "exported"}:
        return "manifest_attr"
    return "string"


def _manifest_component_role(tag: str, name: str, attrs: dict[str, str], component_intents: list[dict[str, object]]) -> str:
    lower = f"{tag} {name} {' '.join(str(row.get('value', '')) for row in component_intents)}".lower()
    if "android.intent.action.main" in lower or "android.intent.category.launcher" in lower or "unity" in lower:
        return "game-launcher"
    if any(term in lower for term in ("push", "hms", "getui", "igexin", "mipush", "honor", "vivo", "oppo", "meizu")):
        return "push-sdk"
    if any(term in lower for term in ("pay", "alipay", "unionpay")):
        return "payment-sdk"
    if any(term in lower for term in ("bytedance", "pangle", "openadsdk", "ttdelegate", "download")):
        return "ad-or-download-sdk"
    if tag == "provider":
        return "content-provider"
    if "permission" in lower:
        return "permission-sdk"
    if attrs.get("exported") == "true":
        return "exported-component"
    return "android-component"


def _manifest_attr(events: list[dict[str, object]], tag: str) -> dict[str, str]:
    for event in events:
        if event.get("tag") == tag:
            attrs = event.get("attrs", {})
            return attrs if isinstance(attrs, dict) else {}
    return {}


def _manifest_intent_value(tag: str, attrs: dict[str, str]) -> tuple[str, str]:
    if tag in {"action", "category"}:
        return tag, attrs.get("name", "")
    if tag == "data":
        parts = []
        for key in ("scheme", "host", "port", "path", "pathPrefix", "pathPattern", "mimeType"):
            if attrs.get(key):
                parts.append(f"{key}={attrs[key]}")
        return "data", "; ".join(parts)
    return tag, attrs.get("name", "")


def _network_security_config_paths(root: Path, value: str) -> list[Path]:
    names: list[str] = []
    if value.startswith("@xml/"):
        names.append(value.removeprefix("@xml/"))
    elif value:
        # Without resources.arsc decoding, a binary reference such as @0x7f0d0000
        # can still be resolved well enough when the expected file exists.
        names.append("network_security_config")
    else:
        names.append("network_security_config")
    paths: list[Path] = []
    for name in dict.fromkeys(names):
        candidate = root / "res" / "xml" / f"{name}.xml"
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _collect_network_security_rows(root: Path, value: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in _network_security_config_paths(root, value):
        try:
            parsed = _parse_android_manifest(path)
        except (OSError, FanxiuResourceError, struct.error):
            continue
        for event in parsed.get("events", []):
            if not isinstance(event, dict) or event.get("event") != "start":
                continue
            attrs = event.get("attrs", {})
            if not isinstance(attrs, dict):
                attrs = {}
            rows.append(
                {
                    "source": path.relative_to(root).as_posix(),
                    "tag": event.get("tag", ""),
                    "cleartext_permitted": attrs.get("cleartextTrafficPermitted", ""),
                    "cert_src": attrs.get("src", ""),
                    "domain": attrs.get("domain", ""),
                    "attrs": "; ".join(f"{key}={value}" for key, value in attrs.items()),
                }
            )
    return rows


def _write_manifest_probe_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    parsed: dict[str, object],
    summary: dict[str, str],
    permission_rows: list[dict[str, object]],
    component_rows: list[dict[str, object]],
    intent_rows: list[dict[str, object]],
    network_security_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
) -> None:
    exported_rows = [row for row in component_rows if row.get("exported") == "true"]
    launcher_rows = [row for row in component_rows if row.get("role") == "game-launcher"]
    network_string_rows = [row for row in string_rows if row.get("category") in {"url_or_domain", "fanxiu_package"}]
    cert_sources = sorted({str(row.get("cert_src", "")) for row in network_security_rows if row.get("cert_src")})
    cleartext_rules = sorted(
        {str(row.get("cleartext_permitted", "")) for row in network_security_rows if row.get("cleartext_permitted")}
    )

    lines = [
        "# 凡修 APK Manifest 探针报告",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        f"- Manifest 格式：{parsed.get('format', '')}",
        "- 说明：这是轻量 AndroidManifest 结构化解析，用来定位权限、入口组件和网络策略；它不反编译方法体。",
        "",
        "## 基本信息",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
    ]
    for key in (
        "package",
        "version_name",
        "version_code",
        "min_sdk",
        "target_sdk",
        "uses_cleartext_traffic",
        "network_security_config",
        "allow_backup",
    ):
        lines.append(f"| {key} | {_markdown_table_cell(summary.get(key, ''))} |")

    lines.extend(
        [
            "",
            "## 关键结论",
            "",
            f"- 权限：{len(permission_rows)} 条；组件：{len(component_rows)} 个；显式 exported=true：{len(exported_rows)} 个；intent/data：{len(intent_rows)} 条。",
            f"- 网络权限：{'有 INTERNET' if any(row.get('name') == 'android.permission.INTERNET' for row in permission_rows) else '未看到 INTERNET'}；明文 HTTP 策略：{summary.get('uses_cleartext_traffic') or '未显式声明'}。",
            f"- Network Security：cleartext={', '.join(cleartext_rules) or '未解析到'}；certificates={', '.join(cert_sources) or '未解析到'}。",
            f"- 启动/Unity 入口候选：{', '.join(str(row.get('name', '')) for row in launcher_rows[:6]) or '未识别'}。",
            "",
            "## 权限",
            "",
            "| 类型 | 名称 | protectionLevel |",
            "| --- | --- | --- |",
        ]
    )
    for row in permission_rows[:80]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('kind', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('protection_level', ''))} |"
        )

    lines.extend(["", "## 组件", "", "| 类型 | 角色 | 名称 | exported | process/authorities |", "| --- | --- | --- | --- | --- |"])
    for row in component_rows[:120]:
        process_or_authorities = row.get("process") or row.get("authorities") or ""
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('type', ''))} | "
            f"{_markdown_table_cell(row.get('role', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''), limit=240)} | "
            f"{_markdown_table_cell(row.get('exported', ''))} | "
            f"{_markdown_table_cell(process_or_authorities, limit=220)} |"
        )

    if network_security_rows:
        lines.extend(
            [
                "",
                "## Network Security Config",
                "",
                "| 来源 | 标签 | cleartext | cert src | attrs |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in network_security_rows[:80]:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('source', ''))} | "
                f"{_markdown_table_cell(row.get('tag', ''))} | "
                f"{_markdown_table_cell(row.get('cleartext_permitted', ''))} | "
                f"{_markdown_table_cell(row.get('cert_src', ''))} | "
                f"{_markdown_table_cell(row.get('attrs', ''), limit=260)} |"
            )

    lines.extend(["", "## Intent / Deep Link", "", "| 组件 | 类型 | 值 |", "| --- | --- | --- |"])
    for row in intent_rows[:120]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('component', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('kind', ''))} | "
            f"{_markdown_table_cell(row.get('value', ''), limit=300)} |"
        )

    lines.extend(["", "## 网络/包名相关字符串", "", "| 分类 | 值 |", "| --- | --- |"])
    for row in network_string_rows[:80]:
        lines.append(f"| {_markdown_table_cell(row.get('category', ''))} | {_markdown_table_cell(row.get('value', ''), limit=300)} |")

    errors = [str(item) for item in parsed.get("errors", []) if str(item)]
    if errors:
        lines.extend(["", "## 解析备注", ""])
        for error in errors[:10]:
            lines.append(f"- {_markdown_table_cell(error, limit=220)}")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_apk_manifest_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = root / "AndroidManifest.xml"
    if not manifest_path.is_file():
        raise FanxiuResourceError(f"缺少 AndroidManifest.xml：{manifest_path}")

    parsed = _parse_android_manifest(manifest_path)
    strings = [str(item) for item in parsed.get("strings", [])]
    events = [event for event in parsed.get("events", []) if isinstance(event, dict)]
    multiconfig = _read_key_value_file(root / "assets" / "multiconfig")
    manifest_attrs = _manifest_attr(events, "manifest")
    sdk_attrs = _manifest_attr(events, "uses-sdk")
    app_attrs = _manifest_attr(events, "application")

    summary = {
        "package": manifest_attrs.get("package", "") or multiconfig.get("pname", ""),
        "version_name": manifest_attrs.get("versionName", "") or multiconfig.get("versionName", ""),
        "version_code": manifest_attrs.get("versionCode", "") or multiconfig.get("versionCode", ""),
        "min_sdk": sdk_attrs.get("minSdkVersion", ""),
        "target_sdk": sdk_attrs.get("targetSdkVersion", "") or multiconfig.get("targetSdkVersion", ""),
        "uses_cleartext_traffic": app_attrs.get("usesCleartextTraffic", ""),
        "network_security_config": app_attrs.get("networkSecurityConfig", ""),
        "allow_backup": app_attrs.get("allowBackup", ""),
    }
    network_security_rows = _collect_network_security_rows(root, summary["network_security_config"])

    string_rows = [
        {"index": index, "category": _manifest_string_category(value), "value": value}
        for index, value in enumerate(strings)
        if value
    ]

    permission_rows: list[dict[str, object]] = []
    seen_permissions: set[tuple[str, str]] = set()
    for event in events:
        tag = str(event.get("tag", ""))
        attrs = event.get("attrs", {})
        if not isinstance(attrs, dict) or tag not in {"uses-permission", "permission", "permission-group"}:
            continue
        name = str(attrs.get("name", ""))
        if not name:
            continue
        kind = "uses" if tag == "uses-permission" else tag.replace("permission-", "")
        key = (kind, name)
        if key in seen_permissions:
            continue
        seen_permissions.add(key)
        permission_rows.append(
            {
                "kind": kind,
                "name": name,
                "protection_level": attrs.get("protectionLevel", ""),
                "line": event.get("line", ""),
            }
        )

    intent_rows: list[dict[str, object]] = []
    for event in events:
        tag = str(event.get("tag", ""))
        attrs = event.get("attrs", {})
        if not isinstance(attrs, dict) or tag not in {"action", "category", "data"}:
            continue
        kind, value = _manifest_intent_value(tag, {str(k): str(v) for k, v in attrs.items()})
        if not value:
            continue
        intent_rows.append(
            {
                "component_type": event.get("parent_component_type", ""),
                "component": event.get("parent_component_name", ""),
                "kind": kind,
                "value": value,
                "line": event.get("line", ""),
            }
        )

    intents_by_component: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for row in intent_rows:
        intents_by_component[str(row.get("component", ""))].append(row)

    component_rows: list[dict[str, object]] = []
    for event in events:
        tag = str(event.get("tag", ""))
        attrs = event.get("attrs", {})
        if tag not in _MANIFEST_COMPONENT_TAGS or not isinstance(attrs, dict):
            continue
        component_name = str(attrs.get("name", ""))
        component_intents = intents_by_component.get(component_name, [])
        component_rows.append(
            {
                "type": tag,
                "role": _manifest_component_role(tag, component_name, {str(k): str(v) for k, v in attrs.items()}, component_intents),
                "name": component_name,
                "exported": attrs.get("exported", ""),
                "enabled": attrs.get("enabled", ""),
                "process": attrs.get("process", ""),
                "authorities": attrs.get("authorities", ""),
                "permission": attrs.get("permission", ""),
                "read_permission": attrs.get("readPermission", ""),
                "write_permission": attrs.get("writePermission", ""),
                "line": event.get("line", ""),
            }
        )

    role_order = {"game-launcher": 0, "exported-component": 1, "push-sdk": 2, "payment-sdk": 3, "ad-or-download-sdk": 4}
    component_rows.sort(key=lambda row: (role_order.get(str(row.get("role", "")), 99), str(row.get("type", "")), str(row.get("name", ""))))
    permission_rows.sort(key=lambda row: (str(row.get("kind", "")), str(row.get("name", ""))))
    intent_rows.sort(key=lambda row: (str(row.get("component", "")), str(row.get("kind", "")), str(row.get("value", ""))))

    _write_tsv(output_dir / "apk_manifest_strings.tsv", ["index", "category", "value"], string_rows)
    _write_tsv(output_dir / "apk_manifest_permissions.tsv", ["kind", "name", "protection_level", "line"], permission_rows)
    _write_tsv(
        output_dir / "apk_manifest_components.tsv",
        [
            "type",
            "role",
            "name",
            "exported",
            "enabled",
            "process",
            "authorities",
            "permission",
            "read_permission",
            "write_permission",
            "line",
        ],
        component_rows,
    )
    _write_tsv(output_dir / "apk_manifest_intents.tsv", ["component_type", "component", "kind", "value", "line"], intent_rows)
    _write_tsv(
        output_dir / "apk_manifest_network_security.tsv",
        ["source", "tag", "cleartext_permitted", "cert_src", "domain", "attrs"],
        network_security_rows,
    )
    _write_manifest_probe_markdown(
        output_dir / "apk_manifest_probe_report.md",
        root=root,
        output_dir=output_dir,
        parsed=parsed,
        summary=summary,
        permission_rows=permission_rows,
        component_rows=component_rows,
        intent_rows=intent_rows,
        network_security_rows=network_security_rows,
        string_rows=string_rows,
    )

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "counts": {
            "strings": len(string_rows),
            "permissions": len(permission_rows),
            "components": len(component_rows),
            "exported_components": sum(1 for row in component_rows if row.get("exported") == "true"),
            "intents": len(intent_rows),
            "network_security_rules": len(network_security_rows),
            "by_component_role": dict(Counter(str(row["role"]) for row in component_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "apk_manifest_probe_report.json"),
            "markdown": str(output_dir / "apk_manifest_probe_report.md"),
            "strings": str(output_dir / "apk_manifest_strings.tsv"),
            "permissions": str(output_dir / "apk_manifest_permissions.tsv"),
            "components": str(output_dir / "apk_manifest_components.tsv"),
            "intents": str(output_dir / "apk_manifest_intents.tsv"),
            "network_security": str(output_dir / "apk_manifest_network_security.tsv"),
        },
    }
    (output_dir / "apk_manifest_probe_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _add_download_config_row(
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str]],
    *,
    category: str,
    source: str,
    key: str,
    value: str,
    note: str,
) -> None:
    clean_key = key.strip().lstrip("#")
    clean_value = value.strip()
    if not clean_key and not clean_value:
        return
    item_key = (category, source, clean_key)
    if item_key in seen:
        return
    seen.add(item_key)
    rows.append(
        {
            "category": category,
            "source": source,
            "key": clean_key,
            "value": clean_value,
            "note": note,
        }
    )


def _download_config_value_for_report(key: str, value: str) -> str:
    if key == "GeTuiParam":
        try:
            payload = json.loads(value)
            datas = payload.get("datas", []) if isinstance(payload, dict) else []
            bundle_ids = [str(item.get("bundleId", "")) for item in datas if isinstance(item, dict) and item.get("bundleId")]
            return f"{len(datas)} push configs; bundleIds={', '.join(bundle_ids[:12])}"
        except json.JSONDecodeError:
            return "<redacted GeTuiParam>"
    lower = key.lower()
    if "secret" in lower or "appkey" in lower:
        return "<redacted>"
    return value


def _download_config_category(key: str, value: str) -> str:
    lower_key = key.lower().lstrip("#")
    lower_value = value.lower()
    if lower_key in {"config_url", "url"} and lower_value.startswith(("http://", "https://")):
        return "bootstrap_url"
    if lower_key.endswith("url") or lower_key.endswith("urls") or lower_key in {"resdownloadurl", "serverlisturl", "noticeurl"}:
        return "url_config"
    if "url" in lower_key and lower_value.startswith(("http://", "https://")):
        return "url_config"
    if lower_value.startswith(("http://", "https://")):
        return "bootstrap_url"
    if "version" in lower_key or lower_key in {"pname", "versioncode", "versionname", "channelsdkversion", "channelname"}:
        return "package_version"
    if lower_key in {"buildtype", "buildpackage", "bundleversion", "forceupdatejson", "nosdk"} or lower_key.startswith("resources"):
        return "build_config"
    if lower_key == "getuiparam":
        return "sdk_config"
    return "config"


def _download_config_note(category: str, key: str) -> str:
    if category == "bootstrap_url":
        return "启动配置 URL，本地 APK 会先从这里拿线上配置或版本信息。"
    if category == "url_config":
        return "Unity urlConfig 中的 URL 项；多半是默认、测试或渠道兜底配置。"
    if category == "package_version":
        return "APK 内置版本/渠道/包名信息。"
    if category == "build_config":
        return "资源下载场景相关构建开关或内置资源版本。"
    if category == "sdk_config":
        return "渠道 SDK 配置摘要，敏感 key 已做脱敏。"
    if category == "resource_manifest":
        return "下载后资源目录里的清单文件状态。"
    return f"本地配置项：{key}"


def _write_download_config_markdown(
    path: Path,
    *,
    root: Path,
    resource_root: Path | None,
    output_dir: Path,
    rows: list[dict[str, object]],
) -> None:
    by_category: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_category[str(row["category"])].append(row)
    category_order = {
        "bootstrap_url": 0,
        "url_config": 1,
        "package_version": 2,
        "build_config": 3,
        "sdk_config": 4,
        "resource_manifest": 5,
        "config": 6,
    }
    lines = [
        "# 凡修 APK 下载配置报告",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 资源目录：`{resource_root}`" if resource_root is not None else "- 资源目录：未纳入",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告只解析本地 APK/资源目录配置，不访问线上服务器。",
        "",
    ]
    for category in sorted(by_category, key=lambda item: category_order.get(item, 99)):
        lines.extend(
            [
                f"## {category}",
                "",
                "| 来源 | 键 | 值 | 备注 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in by_category[category]:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('source', ''))} | "
                f"{_markdown_table_cell(row.get('key', ''))} | "
                f"{_markdown_table_cell(row.get('value', ''), limit=360)} | "
                f"{_markdown_table_cell(row.get('note', ''), limit=180)} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_apk_download_config_report(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root) if resource_root is not None else None
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()

    text_files = {
        "assets/version.txt": "config_url",
        "assets/filelistVersion": "filelistVersion",
        "assets/AppVersion.txt": "AppVersion",
    }
    for rel, key in text_files.items():
        value = _read_text_file_if_exists(root / Path(rel))
        if not value:
            continue
        if key == "config_url":
            value = next((line.strip() for line in value.splitlines() if line.strip()), value)
        category = _download_config_category(key, value)
        _add_download_config_row(
            rows,
            seen,
            category=category,
            source=rel,
            key=key,
            value=_download_config_value_for_report(key, value),
            note=_download_config_note(category, key),
        )

    for key, value in _read_key_value_file(root / "assets" / "multiconfig").items():
        category = _download_config_category(key, value)
        if category not in {"package_version", "build_config", "sdk_config"}:
            continue
        _add_download_config_row(
            rows,
            seen,
            category=category,
            source="assets/multiconfig",
            key=key,
            value=_download_config_value_for_report(key, value),
            note=_download_config_note(category, key),
        )

    data_root = root / "assets" / "bin" / "Data"
    if data_root.is_dir():
        for path in sorted((item for item in data_root.iterdir() if item.is_file()), key=lambda item: item.name.lower()):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not _runtime_scan_file_allowed(path, size):
                continue
            rel = path.relative_to(root).as_posix()
            for text in _read_printable_strings(path):
                stripped = text.strip()
                if not stripped:
                    continue
                if stripped.startswith(("http://", "https://")) and _runtime_url_interesting(stripped):
                    category = _download_config_category("url", stripped)
                    _add_download_config_row(
                        rows,
                        seen,
                        category=category,
                        source=rel,
                        key="url",
                        value=stripped,
                        note=_download_config_note(category, "url"),
                    )
                    continue
                if "=" not in stripped:
                    if stripped.startswith("Resources:"):
                        key, value = stripped.split(":", 1)
                    else:
                        continue
                else:
                    key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key.startswith("<?xml"):
                    continue
                category = _download_config_category(key, value)
                if category not in {"url_config", "package_version", "build_config", "sdk_config"}:
                    continue
                _add_download_config_row(
                    rows,
                    seen,
                    category=category,
                    source=rel,
                    key=key,
                    value=_download_config_value_for_report(key.lstrip("#"), value),
                    note=_download_config_note(category, key),
                )

    if resolved_resource_root is not None:
        setting_config_path = resolved_resource_root / "setting.config"
        for key, value in _read_key_value_file(setting_config_path).items():
            category = _download_config_category(key, value)
            if category not in {"url_config", "package_version", "build_config", "sdk_config"}:
                continue
            _add_download_config_row(
                rows,
                seen,
                category=category,
                source=str(setting_config_path.relative_to(resolved_resource_root).as_posix()),
                key=key,
                value=_download_config_value_for_report(key, value),
                note=_download_config_note(category, key),
            )
        filelist_version = _read_text_file_if_exists(resolved_resource_root / "filelistVersion")
        if filelist_version:
            _add_download_config_row(
                rows,
                seen,
                category="resource_manifest",
                source="filelistVersion",
                key="resource_filelistVersion",
                value=filelist_version,
                note=_download_config_note("resource_manifest", "resource_filelistVersion"),
            )
        filelist_path = resolved_resource_root / "filelist.csv"
        if filelist_path.is_file():
            row_count = max(0, sum(1 for _line in filelist_path.open("r", encoding="utf-8-sig", errors="ignore")) - 1)
            _add_download_config_row(
                rows,
                seen,
                category="resource_manifest",
                source="filelist.csv",
                key="resource_filelist_rows",
                value=f"{row_count} rows; {filelist_path.stat().st_size} bytes",
                note=_download_config_note("resource_manifest", "resource_filelist_rows"),
            )

    category_order = {
        "bootstrap_url": 0,
        "url_config": 1,
        "package_version": 2,
        "build_config": 3,
        "sdk_config": 4,
        "resource_manifest": 5,
        "config": 6,
    }
    rows.sort(key=lambda row: (category_order.get(str(row["category"]), 99), str(row["source"]), str(row["key"])))

    fields = ["category", "source", "key", "value", "note"]
    row_count = _write_tsv(output_dir / "apk_download_config_entries.tsv", fields, rows)
    _write_download_config_markdown(
        output_dir / "apk_download_config_report.md",
        root=root,
        resource_root=resolved_resource_root,
        output_dir=output_dir,
        rows=rows,
    )
    result = {
        "apk_root": str(root),
        "resource_root": str(resolved_resource_root) if resolved_resource_root is not None else "",
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "entries": row_count,
            "by_category": dict(Counter(str(row["category"]) for row in rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "apk_download_config_report.json"),
            "markdown": str(output_dir / "apk_download_config_report.md"),
            "entries": str(output_dir / "apk_download_config_entries.tsv"),
        },
    }
    (output_dir / "apk_download_config_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_fanxiu_apk_runtime_entry_report(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_rows: int = 500,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    required_index_files = [
        output_dir / "native_libs.tsv",
        output_dir / "asset_filelist.tsv",
        output_dir / "dex_keyword_hits.tsv",
    ]
    if not all(path.is_file() for path in required_index_files):
        build_fanxiu_apk_static_index(
            apk_root=root,
            export_root=export_base,
            keywords=APK_RUNTIME_ENTRY_SCAN_KEYWORDS,
            keyword_hit_limit=60000,
        )

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    category_counts: Counter[str] = Counter()

    _add_runtime_url_candidates(root, rows, seen, category_counts)
    _add_runtime_symbol_candidates(root, rows, seen, category_counts)
    _add_runtime_native_candidates(output_dir, rows, seen, category_counts)
    _add_runtime_il2cpp_candidates(output_dir, rows, seen, category_counts)
    _add_runtime_dex_candidates(output_dir, rows, seen, category_counts)
    _add_runtime_asset_path_candidates(output_dir, rows, seen, category_counts)

    rows.sort(
        key=lambda row: (
            -int(row.get("confidence") or 0),
            _APK_RUNTIME_CATEGORY_ORDER.get(str(row.get("category", "")), 999),
            str(row.get("source", "")).lower(),
            str(row.get("name", "")).lower(),
            str(row.get("value", "")).lower(),
        )
    )
    limited_rows = rows[: max(1, int(max_rows))]

    fields = ["category", "confidence", "source", "keyword", "name", "value", "note"]
    candidate_count = _write_tsv(output_dir / "apk_runtime_entry_candidates.tsv", fields, limited_rows)
    _write_runtime_entry_markdown(output_dir / "apk_runtime_entry_report.md", root=root, output_dir=output_dir, rows=limited_rows)

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "candidates": candidate_count,
            "all_candidates_before_limit": len(rows),
            "by_category": dict(Counter(str(row["category"]) for row in limited_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "apk_runtime_entry_report.json"),
            "markdown": str(output_dir / "apk_runtime_entry_report.md"),
            "candidates": str(output_dir / "apk_runtime_entry_candidates.tsv"),
        },
    }
    (output_dir / "apk_runtime_entry_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_fanxiu_apk_static_index(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    keywords: Iterable[str] | None = None,
    keyword_hit_limit: int = 30000,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized_keywords = tuple(dict.fromkeys(str(item) for item in (keywords or APK_INDEX_DEFAULT_KEYWORDS) if str(item).strip()))
    file_summary = _write_apk_files(root, output_dir)
    native_lib_count = _write_native_libs(root, output_dir)
    unity_file_count = _write_unity_files(root, output_dir)
    asset_filelist_count = _write_asset_filelists(root, output_dir)
    asset_keyword_hit_count = _write_asset_keyword_hits(root, output_dir, normalized_keywords, limit=10000)
    dex_summary = _write_dex_indexes(root, output_dir, normalized_keywords, keyword_hit_limit=keyword_hit_limit)

    multiconfig = _read_key_value_file(root / "assets" / "multiconfig")
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "package": multiconfig.get("pname", ""),
        "version_name": multiconfig.get("versionName", ""),
        "version_code": multiconfig.get("versionCode", ""),
        "target_sdk_version": multiconfig.get("targetSdkVersion", ""),
        "multiconfig": multiconfig,
        "counts": {
            "apk_files": file_summary["file_count"],
            "apk_file_bytes": file_summary["total_bytes"],
            "native_libs": native_lib_count,
            "unity_files": unity_file_count,
            "asset_filelist_rows": asset_filelist_count,
            "asset_keyword_hits": asset_keyword_hit_count,
            "dex_keyword_hits": dex_summary["keyword_hit_count"],
            "dex_files": len(dex_summary["files"]),
            "dex_errors": len(dex_summary["errors"]),
        },
        "files": file_summary,
        "dex": dex_summary,
        "outputs": {
            "summary": str(output_dir / "summary.json"),
            "apk_files": str(output_dir / "apk_files.tsv"),
            "native_libs": str(output_dir / "native_libs.tsv"),
            "unity_files": str(output_dir / "unity_files.tsv"),
            "asset_filelist": str(output_dir / "asset_filelist.tsv"),
            "asset_keyword_hits": str(output_dir / "asset_keyword_hits.tsv"),
            "dex_strings": str(output_dir / "dex_strings.tsv"),
            "dex_classes": str(output_dir / "dex_classes.tsv"),
            "dex_methods": str(output_dir / "dex_methods.tsv"),
            "dex_keyword_hits": str(output_dir / "dex_keyword_hits.tsv"),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


_APK_NETWORK_TERM_SPECS = (
    ("url", ("https://", "http://"), 90, "静态 URL 或下载配置，可直接用于归类域名/资源入口。"),
    ("unity_http_loader", ("CoroutineHttpLoader", "HttpDownload", "HttpLoader", "UnityWebRequest"), 85, "Unity/IL2CPP 层 HTTP 加载入口。"),
    ("java_http_client", ("okhttp", "HttpURLConnection", "org.apache.http", "httpclient"), 70, "Android Java/Dex 层 HTTP 客户端或 SDK 依赖。"),
    ("dotnet_http_client", ("HttpWebRequest", "System.Net.Http", "WebRequest"), 70, "IL2CPP/.NET 网络 API 符号。"),
    ("socket_bridge", ("SocketBridge", "System.Net.Sockets", "java.net.Socket", "WebSocket", "TcpClient"), 75, "Socket 或 Lua/native 桥接能力，需要继续找运行时调用点。"),
    ("tls_cert", ("X509TrustManager", "checkServerTrusted", "HostnameVerifier", "CertificatePinner", "TrustManager"), 70, "TLS/证书校验相关符号，可用于判断是否存在证书固定或自定义信任逻辑。"),
    ("tls_runtime", ("SSLSocketFactory", "SSLContext", "sslSocketFactory", "Mono.Security.X509"), 60, "TLS 运行库或证书处理基础设施。"),
    ("proxy", ("ProxySelector", "get_Proxy", "set_Proxy", "proxy=", "proxySelector="), 55, "代理相关 API 或调试字符串；可能来自通用库。"),
)


def _network_term_matches(text: str) -> list[tuple[str, str, int, str]]:
    lower = text.lower()
    matches: list[tuple[str, str, int, str]] = []
    for category, terms, confidence, note in _APK_NETWORK_TERM_SPECS:
        for term in terms:
            if term.lower() in lower:
                matches.append((category, term, confidence, note))
    return matches


def _add_network_stack_row(
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    *,
    category: str,
    confidence: int,
    source_table: str,
    source: str,
    term: str,
    symbol: str,
    evidence: str,
    note: str,
) -> None:
    key = (category, source_table, source, symbol or evidence)
    if key in seen:
        return
    seen.add(key)
    rows.append(
        {
            "category": category,
            "confidence": confidence,
            "source_table": source_table,
            "source": source,
            "term": term,
            "symbol": symbol,
            "evidence": evidence,
            "note": note,
        }
    )


def _collect_network_stack_tsv_hits(
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    *,
    output_dir: Path,
    table_name: str,
    value_fields: tuple[str, ...],
    source_fields: tuple[str, ...] = (),
    row_limit: int = 300,
) -> None:
    path = output_dir / table_name
    for row in _read_tsv_rows(path):
        evidence_parts = [str(row.get(field, "")) for field in value_fields if row.get(field)]
        if not evidence_parts:
            continue
        evidence = " | ".join(evidence_parts)
        matches = _network_term_matches(evidence)
        if not matches:
            continue
        source = " | ".join(str(row.get(field, "")) for field in source_fields if row.get(field))
        if not source:
            source = str(row.get("source", "") or row.get("dex", "") or row.get("index", ""))
        symbol = str(row.get("qualified_name", "") or row.get("java_name", "") or row.get("value", "") or row.get("name", ""))
        for category, term, confidence, note in matches:
            _add_network_stack_row(
                rows,
                seen,
                category=category,
                confidence=confidence,
                source_table=table_name,
                source=source,
                term=term,
                symbol=symbol,
                evidence=evidence,
                note=note,
            )
            if len(rows) >= row_limit:
                return


def _write_network_stack_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    rows: list[dict[str, object]],
    manifest_summary: dict[str, str],
) -> None:
    by_category: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_category[str(row["category"])].append(row)
    category_counts = Counter(str(row["category"]) for row in rows)
    top_domains = []
    for row in rows:
        evidence = str(row.get("evidence", ""))
        for url in _URL_BYTES_RE.findall(evidence.encode("utf-8", errors="ignore")):
            text = url.decode("utf-8", errors="ignore")
            host = re.sub(r"^https?://", "", text).split("/", 1)[0]
            if host and host not in top_domains:
                top_domains.append(host)

    cleartext = manifest_summary.get("uses_cleartext_traffic", "")
    network_config = manifest_summary.get("network_security_config", "")
    lines = [
        "# 凡修 APK 网络栈静态探针",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告合并 Manifest、下载配置、DEX 字符串、IL2CPP metadata 和运行入口候选；只做静态归类，不访问服务器。",
        "",
        "## 结论",
        "",
        f"- Manifest 网络策略：usesCleartextTraffic={cleartext or '未声明'}，networkSecurityConfig={network_config or '未声明'}。",
        f"- 命中分类：{', '.join(f'{key}:{value}' for key, value in category_counts.most_common()) or '无'}。",
        f"- URL/域名样例：{', '.join(top_domains[:12]) or '无'}。",
    ]
    if category_counts.get("tls_cert"):
        lines.append("- 发现 TLS/证书校验相关符号；这只能说明存在相关库或接口，是否证书固定需要继续看调用点或运行时行为。")
    if category_counts.get("socket_bridge"):
        lines.append("- 发现 Socket/Lua bridge 符号；后续应从 Lua 调用点判断登录、战斗或长连接是否走该通道。")
    if category_counts.get("unity_http_loader"):
        lines.append("- Unity HTTP loader 命中较明确，资源下载和部分配置请求优先从这条链路继续追。")

    lines.extend(["", "## 高置信命中", "", "| 分类 | 置信度 | 来源 | 符号/值 | 备注 |", "| --- | --- | --- | --- | --- |"])
    for row in rows[:80]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('category', ''))} | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('source_table', ''))}:{_markdown_table_cell(row.get('source', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('symbol') or row.get('evidence'), limit=260)} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=180)} |"
        )

    lines.extend(["", "## 分类明细", ""])
    for category in sorted(by_category):
        lines.extend([f"### {category}", "", "| 来源 | term | evidence |", "| --- | --- | --- |"])
        for row in by_category[category][:60]:
            lines.append(
                "| "
                f"{_markdown_table_cell(row.get('source_table', ''))}:{_markdown_table_cell(row.get('source', ''), limit=160)} | "
                f"{_markdown_table_cell(row.get('term', ''))} | "
                f"{_markdown_table_cell(row.get('evidence', ''), limit=320)} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_apk_network_stack_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_rows: int = 1000,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not (output_dir / "dex_strings.tsv").is_file():
        build_fanxiu_apk_static_index(apk_root=root, export_root=export_base, keyword_hit_limit=60000)
    manifest_result = build_fanxiu_apk_manifest_probe(apk_root=root, export_root=export_base)
    if resource_root is not None or not (output_dir / "apk_download_config_entries.tsv").is_file():
        build_fanxiu_apk_download_config_report(apk_root=root, resource_root=resource_root, export_root=export_base)
    if not (output_dir / "apk_runtime_entry_candidates.tsv").is_file():
        build_fanxiu_apk_runtime_entry_report(apk_root=root, export_root=export_base, max_rows=800)

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for row in _read_tsv_rows(output_dir / "apk_manifest_network_security.tsv"):
        evidence = " | ".join(str(row.get(field, "")) for field in ("tag", "cleartext_permitted", "cert_src", "attrs") if row.get(field))
        _add_network_stack_row(
            rows,
            seen,
            category="manifest_network_policy",
            confidence=95,
            source_table="apk_manifest_network_security.tsv",
            source=str(row.get("source", "")),
            term=str(row.get("tag", "")),
            symbol=evidence,
            evidence=evidence,
            note="Manifest network-security-config 解析结果。",
        )
    for row in _read_tsv_rows(output_dir / "apk_manifest_permissions.tsv"):
        if row.get("name") in {"android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE"}:
            _add_network_stack_row(
                rows,
                seen,
                category="manifest_network_policy",
                confidence=95,
                source_table="apk_manifest_permissions.tsv",
                source=str(row.get("line", "")),
                term=str(row.get("name", "")),
                symbol=str(row.get("name", "")),
                evidence=str(row.get("name", "")),
                note="Manifest 网络权限。",
            )

    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="apk_download_config_entries.tsv",
        value_fields=("key", "value", "note"),
        source_fields=("source",),
        row_limit=max_rows,
    )
    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="apk_runtime_entry_candidates.tsv",
        value_fields=("keyword", "name", "value", "note"),
        source_fields=("category", "source"),
        row_limit=max_rows,
    )
    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="dex_strings.tsv",
        value_fields=("value",),
        source_fields=("dex", "index"),
        row_limit=max_rows,
    )
    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="dex_methods.tsv",
        value_fields=("qualified_name", "class_name", "name"),
        source_fields=("dex", "index"),
        row_limit=max_rows,
    )
    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="dex_classes.tsv",
        value_fields=("java_name", "package", "short_name"),
        source_fields=("dex", "index"),
        row_limit=max_rows,
    )
    _collect_network_stack_tsv_hits(
        rows,
        seen,
        output_dir=output_dir,
        table_name="il2cpp_keyword_hits.tsv",
        value_fields=("kind", "keyword", "value"),
        source_fields=("kind", "index"),
        row_limit=max_rows,
    )

    rows.sort(
        key=lambda row: (
            -int(row.get("confidence") or 0),
            str(row.get("category", "")),
            str(row.get("source_table", "")),
            str(row.get("source", "")),
            str(row.get("symbol", "")),
        )
    )
    limited_rows = rows[: max(1, int(max_rows))]
    fields = ["category", "confidence", "source_table", "source", "term", "symbol", "evidence", "note"]
    row_count = _write_tsv(output_dir / "apk_network_stack_hits.tsv", fields, limited_rows)
    _write_network_stack_markdown(
        output_dir / "apk_network_stack_report.md",
        root=root,
        output_dir=output_dir,
        rows=limited_rows,
        manifest_summary={str(k): str(v) for k, v in dict(manifest_result.get("summary", {})).items()},
    )

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "hits": row_count,
            "all_hits_before_limit": len(rows),
            "by_category": dict(Counter(str(row["category"]) for row in limited_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "apk_network_stack_report.json"),
            "markdown": str(output_dir / "apk_network_stack_report.md"),
            "hits": str(output_dir / "apk_network_stack_hits.tsv"),
        },
    }
    (output_dir / "apk_network_stack_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_LOGIN_SERVER_CONFIG_KEYS = {
    "serverlisturl",
    "servercheckurl",
    "importserverurl",
    "testlogincheckinurl",
    "appointurl",
    "noticeurl",
    "buildtype",
    "nosdk",
    "buildpackage",
    "bundleversion",
    "ip",
    "port",
    "servername",
    "serverid",
    "id",
    "channelpackage",
    "loginid",
    "username",
}

_LOGIN_SERVER_LUA_FILES = {
    "GetServerInfo.lua",
    "LoginModel.lua",
    "LoginData.lua",
    "LoginServer.lua",
    "LoginMgr.lua",
    "EnterGameInfo.lua",
    "ServerListDataMode.lua",
    "StartGameInfo.lua",
    "SocketManager.lua",
    "LuaSocket.lua",
    "AutoRelink.lua",
}

_LOGIN_SERVER_FLOW_PATTERNS = (
    ("01-config", "HTTP server-list URL", "setting.config", "ServerListUrl=", "服务器列表 HTTP 入口。"),
    ("01-config", "HTTP server-check URL", "setting.config", "ServerCheckUrl=", "进服前区服状态检查入口。"),
    ("02-request", "GetSDKServerList request", "GetServerInfo.lua", "function _M.GetSDKServerList", "Lua 侧组装服务器列表请求。"),
    ("02-request", "GameLoginBridge.F_GetServerList", "GetServerInfo.lua", "GameLoginBridge.F_GetServerList", "Lua 调 C#/IL2CPP bridge 发起服务器列表请求。"),
    ("03-response", "GET_SERVER_LIST_SUCCEED", "GetServerInfo.lua", "GET_SERVER_LIST_SUCCEED", "服务器列表回调后广播 JSON 数据。"),
    ("04-decode", "LoginData decode", "LoginModel.lua", "LuaUtil.decode(jsonStr,typeof(LoginData))", "服务器列表 JSON 被解码成 LoginData。"),
    ("05-schema", "LoginData servers", "LoginData.lua", "self:ServerInfo(data.servers)", "响应 data.servers 进入 LoginServer 列表。"),
    ("05-schema", "server host", "LoginServer.lua", "self.V_Host=data.host", "区服 host 字段映射到 LoginServer.V_Host。"),
    ("05-schema", "server port", "LoginServer.lua", "self.V_Port=data.port", "区服 port 字段映射到 LoginServer.V_Port。"),
    ("06-enter", "IntoGame host/port", "LoginMgr.lua", "sd.V_Host,sd.V_Port", "选中区服的 host/port 被传入进服流程。"),
    ("07-state", "SetServerData", "EnterGameInfo.lua", "LoginMgr.Inst_get():SetServerData", "进服开始时保存当前区服。"),
    ("07-state", "runtime domain", "LoginModel.lua", "serverItem.domain=serverIp", "serverIp 被保存为运行时 socket domain。"),
    ("07-state", "runtime port", "LoginModel.lua", "serverItem.port=serverPort", "serverPort 被保存为运行时 socket port。"),
    ("08-socket", "SocketConnect", "EnterGameInfo.lua", "self:SocketConnect()", "保存区服后开始 socket 连接。"),
    ("08-socket", "SocketManager.F_InitSocketCon", "SocketManager.lua", "function _M.F_InitSocketCon", "SocketManager 负责选择主 socket 并发起连接。"),
    ("08-socket", "LuaSocket.F_Connect", "SocketManager.lua", "so:F_Connect(pServer,pPort,pIslogin)", "SocketManager 把 pServer/pPort 下传给 LuaSocket。"),
    ("09-native", "SocketBridge.F_Connect", "LuaSocket.lua", "SocketBridge.F_Connect(pIp,pPort or 0,pIslogin,self.isMainSocket)", "LuaSocket 最终进入 native/IL2CPP SocketBridge。"),
    ("10-check", "Server status check", "GetServerInfo.lua", "GameLoginBridge.F_ServerStatusCheck", "进服前可单独检查区服状态。"),
)


def _login_server_canonical_lua_name(path: Path) -> str:
    return re.sub(r"__-?\d+(?=\.lua$)", "", path.name)


def _login_server_config_value_for_report(key: str, value: str) -> str:
    lower = key.lower()
    if lower in {"loginid", "username"} or "token" in lower or "account" in lower or "sign" in lower:
        return "<redacted>"
    return _download_config_value_for_report(key, value)


def _login_server_config_note(source: str, key: str) -> str:
    lower = key.lower()
    if lower == "serverlisturl":
        return "服务器列表 HTTP 入口；返回数据里的 servers[].host/port 会继续进入 socket 链路。"
    if lower == "servercheckurl":
        return "进服前区服状态检查 HTTP 入口。"
    if lower in {"importserverurl", "testlogincheckinurl"}:
        return "SDK 登录校验/导量入口，和区服 socket 连接不是同一层。"
    if lower in {"ip", "port", "servername", "serverid", "id", "channelpackage"}:
        return "模拟器本地缓存的最近区服选择，用于本地恢复或调试观察。"
    if lower in {"loginid", "username"}:
        return "本地账号标识；报告中已脱敏。"
    if source == "setting.config":
        return "下载后资源根目录配置。"
    return "登录/区服相关配置。"


def _read_login_server_config_rows(resource_root: Path | None) -> list[dict[str, object]]:
    if resource_root is None:
        return []
    rows: list[dict[str, object]] = []
    for file_name in ("setting.config", "luasetting.config"):
        path = resource_root / file_name
        if not path.is_file():
            continue
        for line_no, raw_line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.lower() not in _LOGIN_SERVER_CONFIG_KEYS:
                continue
            rows.append(
                {
                    "source": file_name,
                    "line": line_no,
                    "key": key,
                    "value": _login_server_config_value_for_report(key, value.strip()),
                    "note": _login_server_config_note(file_name, key),
                }
            )
    return rows


def _iter_login_server_lua_files(export_base: Path) -> list[Path]:
    by_source = export_base / "by_source"
    if not by_source.is_dir():
        return []
    best_by_canonical_path: dict[tuple[Path, str], Path] = {}
    for path in by_source.rglob("*.lua"):
        canonical = _login_server_canonical_lua_name(path)
        if canonical not in _LOGIN_SERVER_LUA_FILES:
            continue
        key = (path.parent, canonical)
        existing = best_by_canonical_path.get(key)
        if existing is None or (existing.name != canonical and path.name == canonical):
            best_by_canonical_path[key] = path
    files = list(best_by_canonical_path.values())
    files.sort(key=lambda p: (str(p.parent), _login_server_canonical_lua_name(p), p.name))
    return files


def _lua_report_source(path: Path, export_base: Path) -> str:
    try:
        return str(path.relative_to(export_base)).replace("\\", "/")
    except ValueError:
        return str(path)


def _lua_function_context(lines: list[str], line_index: int) -> str:
    for i in range(line_index, -1, -1):
        match = re.search(r"function\s+_M\.([A-Za-z_]\w*)", lines[i])
        if match:
            return match.group(1)
    return ""


def _add_login_flow_step(
    rows: list[dict[str, object]],
    seen: set[tuple[str, str, str, str]],
    *,
    step: str,
    layer: str,
    source: str,
    line: int,
    symbol: str,
    evidence: str,
    note: str,
) -> None:
    key = (step, source, symbol, evidence.strip())
    if key in seen:
        return
    seen.add(key)
    rows.append(
        {
            "step": step,
            "layer": layer,
            "source": source,
            "line": line,
            "symbol": symbol,
            "evidence": evidence.strip(),
            "note": note,
        }
    )


def _collect_login_server_flow_steps(
    *,
    export_base: Path,
    resource_root: Path | None,
    lua_files: list[Path],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()

    if resource_root is not None:
        for step, symbol, file_name, pattern, note in _LOGIN_SERVER_FLOW_PATTERNS:
            path = resource_root / file_name
            if not path.is_file():
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                if pattern in line:
                    _add_login_flow_step(
                        rows,
                        seen,
                        step=step,
                        layer="config",
                        source=file_name,
                        line=line_no,
                        symbol=symbol,
                        evidence=line,
                        note=note,
                    )

    for path in lua_files:
        canonical = _login_server_canonical_lua_name(path)
        text_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        source = _lua_report_source(path, export_base)
        for step, symbol, file_name, pattern, note in _LOGIN_SERVER_FLOW_PATTERNS:
            if file_name != canonical:
                continue
            for line_no, line in enumerate(text_lines, start=1):
                if pattern in line:
                    _add_login_flow_step(
                        rows,
                        seen,
                        step=step,
                        layer="lua",
                        source=source,
                        line=line_no,
                        symbol=symbol,
                        evidence=line,
                        note=note,
                    )
                    break
    rows.sort(key=lambda row: (str(row["step"]), str(row["source"]), int(row["line"] or 0), str(row["symbol"])))
    return rows


def _collect_login_server_schema_rows(*, export_base: Path, lua_files: list[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(
        *,
        object_name: str,
        target_field: str,
        source_field: str,
        source: str,
        line: int,
        evidence: str,
        note: str,
    ) -> None:
        key = (object_name, target_field, source_field, evidence.strip())
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "object": object_name,
                "target_field": target_field,
                "source_field": source_field,
                "source": source,
                "line": line,
                "evidence": evidence.strip(),
                "note": note,
            }
        )

    request_fields = {"pid", "token", "bundleId", "bundleVersion", "gzip", "cid", "gid", "server"}
    for path in lua_files:
        canonical = _login_server_canonical_lua_name(path)
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        source = _lua_report_source(path, export_base)
        for index, line in enumerate(lines):
            line_no = index + 1
            context = _lua_function_context(lines, index)
            compact = line.strip()
            if canonical == "GetServerInfo.lua" and context in {"GetSDKServerList", "ServerCheck"}:
                match = re.match(r"([A-Za-z_]\w*)\s*=\s*([^,]+),?\s*$", compact)
                if match and match.group(1) in request_fields:
                    add(
                        object_name=f"request.{context}",
                        target_field=match.group(1),
                        source_field=match.group(2).strip(),
                        source=source,
                        line=line_no,
                        evidence=line,
                        note="发送给 GameLoginBridge 的 JSON 请求字段。",
                    )
            if canonical == "LoginData.lua":
                for source_field, target_field in (
                    ("data.servers", "V_Servers/LoginServer[]"),
                    ("data.roles", "V_Roles/LoginRole[]"),
                    ("data.groups", "V_Groups/LoginGroup[]"),
                    ("data.messages", "V_Message"),
                ):
                    if source_field in line:
                        add(
                            object_name="response.LoginData",
                            target_field=target_field,
                            source_field=source_field,
                            source=source,
                            line=line_no,
                            evidence=line,
                            note="服务器列表 JSON 解码后的顶层数据分发。",
                        )
            if canonical == "LoginServer.lua":
                match = re.search(r"self\.V_([A-Za-z_]\w*)\s*=\s*data\.([A-Za-z_]\w*)", line)
                if match:
                    add(
                        object_name="response.LoginServer",
                        target_field=f"V_{match.group(1)}",
                        source_field=f"data.{match.group(2)}",
                        source=source,
                        line=line_no,
                        evidence=line,
                        note="单个区服对象字段映射；host/port 是后续 socket 目标。"
                        if match.group(2) in {"host", "port"}
                        else "单个区服对象字段映射。",
                    )
            if canonical == "LoginModel.lua":
                match = re.search(r"serverItem\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)", line)
                if match:
                    add(
                        object_name="runtime.ServerData",
                        target_field=match.group(1),
                        source_field=match.group(2),
                        source=source,
                        line=line_no,
                        evidence=line,
                        note="进入游戏前缓存当前区服，供 SocketConnect 使用。",
                    )
            if canonical == "LoginMgr.lua" and "sd.V_Host,sd.V_Port" in line:
                add(
                    object_name="runtime.IntoGame",
                    target_field="serverIp/serverPort",
                    source_field="LoginServer.V_Host/LoginServer.V_Port",
                    source=source,
                    line=line_no,
                    evidence=line,
                    note="选中的区服地址被传入 EnterGameInfo.StartEnter_1。",
                )
            if canonical == "LuaSocket.lua" and "SocketBridge.F_Connect(" in line:
                add(
                    object_name="native.SocketBridge",
                    target_field="connect_target",
                    source_field="pIp/pPort",
                    source=source,
                    line=line_no,
                    evidence=line,
                    note="最终 native socket 连接目标。",
                )

    rows.sort(key=lambda row: (str(row["object"]), str(row["target_field"]), str(row["source"]), int(row["line"] or 0)))
    return rows


def _write_login_server_flow_markdown(
    path: Path,
    *,
    apk_root: Path,
    resource_root: Path | None,
    export_base: Path,
    output_dir: Path,
    config_rows: list[dict[str, object]],
    schema_rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
) -> None:
    config_by_key = {str(row["key"]): str(row["value"]) for row in config_rows}
    key_schema = [
        row
        for row in schema_rows
        if str(row["target_field"]) in {"V_Host", "V_Port", "domain", "port", "serverIp/serverPort", "connect_target"}
        or str(row["source_field"]) in {"data.host", "data.port", "LoginServer.V_Host/LoginServer.V_Port", "pIp/pPort"}
    ]
    lines = [
        "# 凡修登录区服到 Socket 链路探针",
        "",
        f"- APK 解包目录：`{apk_root}`",
        f"- 资源目录：`{resource_root or ''}`",
        f"- 导出目录：`{output_dir}`",
        "- 说明：本报告只读取本地 APK/资源导出和 Lua 文本，不访问线上服务器。",
        "",
        "## 结论",
        "",
        f"- 服务器列表入口：`{config_by_key.get('ServerListUrl', '未在本地配置中发现')}`。",
        f"- 区服检查入口：`{config_by_key.get('ServerCheckUrl', '未在本地配置中发现')}`。",
        "- 登录前服务器列表/公告/校验是 HTTP(S) 配置链路；真正进入区服后，`servers[].host/port` 会转成主 socket 的 `domain/port`。",
        "- 当前静态证据链为：`GetSDKServerList -> GameLoginBridge.F_GetServerList -> LoginData/LoginServer -> IntoGame(sd.V_Host, sd.V_Port) -> SetServerData(domain, port) -> SocketBridge.F_Connect`。",
        "",
        "## 关键字段映射",
        "",
        "| 对象 | 目标字段 | 来源字段 | 位置 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in key_schema:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('object', ''))} | "
            f"{_markdown_table_cell(row.get('target_field', ''))} | "
            f"{_markdown_table_cell(row.get('source_field', ''))} | "
            f"{_markdown_table_cell(row.get('source', ''))}:{_markdown_table_cell(row.get('line', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=180)} |"
        )

    lines.extend(["", "## 登录/区服配置", "", "| 来源 | 行 | key | value | 说明 |", "| --- | --- | --- | --- | --- |"])
    for row in config_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('source', ''))} | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"{_markdown_table_cell(row.get('key', ''))} | "
            f"{_markdown_table_cell(row.get('value', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=180)} |"
        )

    lines.extend(["", "## 流程证据", "", "| 步骤 | 层 | 位置 | 符号 | 证据 |", "| --- | --- | --- | --- | --- |"])
    for row in flow_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('step', ''))} | "
            f"{_markdown_table_cell(row.get('layer', ''))} | "
            f"{_markdown_table_cell(row.get('source', ''), limit=190)}:{_markdown_table_cell(row.get('line', ''))} | "
            f"{_markdown_table_cell(row.get('symbol', ''), limit=140)} | "
            f"{_markdown_table_cell(row.get('evidence', ''), limit=260)} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_apk_login_server_flow_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    resource_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    resolved_resource_root = resolve_fanxiu_resource_root(resource_root) if resource_root is not None else None
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not (output_dir / "apk_download_config_entries.tsv").is_file() and resolved_resource_root is not None:
        build_fanxiu_apk_download_config_report(
            apk_root=root,
            resource_root=resolved_resource_root,
            export_root=export_base,
        )

    config_rows = _read_login_server_config_rows(resolved_resource_root)
    lua_files = _iter_login_server_lua_files(export_base)
    flow_rows = _collect_login_server_flow_steps(
        export_base=export_base,
        resource_root=resolved_resource_root,
        lua_files=lua_files,
    )
    schema_rows = _collect_login_server_schema_rows(export_base=export_base, lua_files=lua_files)

    config_count = _write_tsv(
        output_dir / "apk_login_server_config.tsv",
        ["source", "line", "key", "value", "note"],
        config_rows,
    )
    schema_count = _write_tsv(
        output_dir / "apk_login_server_schema.tsv",
        ["object", "target_field", "source_field", "source", "line", "evidence", "note"],
        schema_rows,
    )
    flow_count = _write_tsv(
        output_dir / "apk_login_server_flow_steps.tsv",
        ["step", "layer", "source", "line", "symbol", "evidence", "note"],
        flow_rows,
    )
    _write_login_server_flow_markdown(
        output_dir / "apk_login_server_flow_report.md",
        apk_root=root,
        resource_root=resolved_resource_root,
        export_base=export_base,
        output_dir=output_dir,
        config_rows=config_rows,
        schema_rows=schema_rows,
        flow_rows=flow_rows,
    )

    result = {
        "apk_root": str(root),
        "resource_root": str(resolved_resource_root) if resolved_resource_root else "",
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "config_rows": config_count,
            "schema_rows": schema_count,
            "flow_steps": flow_count,
            "lua_files": len(lua_files),
        },
        "outputs": {
            "summary": str(output_dir / "apk_login_server_flow_report.json"),
            "markdown": str(output_dir / "apk_login_server_flow_report.md"),
            "config": str(output_dir / "apk_login_server_config.tsv"),
            "schema": str(output_dir / "apk_login_server_schema.tsv"),
            "flow_steps": str(output_dir / "apk_login_server_flow_steps.tsv"),
        },
    }
    (output_dir / "apk_login_server_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_DEX_LOGIN_SURFACE_TERM_SPECS = (
    ("game_package", ("com.sy.frxxz", "frxxz"), 90, "游戏包名/R 资源壳线索。"),
    ("shell_activity", ("FlameUnityActivity", "com.flamePhoenix.plugin", "SDKPlugin", "SDKCallback"), 90, "Flame 外壳 Activity/SDK 插件层。"),
    ("sdk_login", ("SDKLogin", "OnSDKLoginData", "BackToGameLogin", "setBackToGameLoginListener"), 88, "Java SDK 登录回调面。"),
    ("channel_sdk", ("com.sqwan", "SQwanCore", "PluginSQwanCore", "SQSdkInterface"), 85, "渠道 SDK 外壳。"),
    ("eyugame_plugin", ("com.eyugame", "bgdownload"), 76, "EyuGame 背景下载/渠道插件。"),
    ("unity_shell", ("UnityPlayerActivity", "UnityPlayer"), 78, "Unity Android 外壳入口。"),
    ("native_runtime", ("java.lang.System.loadLibrary", "loadLibrary"), 72, "Java 侧 native 库加载入口；不能证明具体加载参数。"),
    ("oauth_social", ("WXEntryActivity", "Wechat", "weixin", "qqapi://", "com.tencent.mm.opensdk"), 70, "微信/QQ 等社交 OAuth 登录能力。"),
    ("one_click_auth", ("com.mobile.auth", "LoginToken", "cmpassport", "gatewayauth"), 68, "手机号一键登录/运营商认证 SDK。"),
    ("payment_sdk", ("alipay", "unionpay", "UPPay"), 60, "支付 SDK 依赖，通常与登录链路相邻但不是游戏服通信入口。"),
    ("url_literal", ("http://", "https://", "authurl", "oauth"), 55, "DEX 明文 URL/认证字符串。"),
    ("taptap_surface", ("taptap", "tapdb", "xindong"), 50, "TapTap/TapDB 相关明文或类名。"),
)

_DEX_LOGIN_SURFACE_CATEGORY_ORDER = {
    "game_package": 0,
    "shell_activity": 1,
    "sdk_login": 2,
    "channel_sdk": 3,
    "eyugame_plugin": 4,
    "unity_shell": 5,
    "native_runtime": 6,
    "oauth_social": 7,
    "one_click_auth": 8,
    "payment_sdk": 9,
    "url_literal": 10,
    "taptap_surface": 11,
}

_DEX_LOGIN_SURFACE_CATEGORY_LIMITS = {
    "game_package": 40,
    "shell_activity": 80,
    "sdk_login": 60,
    "channel_sdk": 80,
    "eyugame_plugin": 50,
    "unity_shell": 45,
    "native_runtime": 40,
    "oauth_social": 70,
    "one_click_auth": 70,
    "payment_sdk": 60,
    "url_literal": 80,
    "taptap_surface": 40,
}

_DEX_LOGIN_PACKAGE_PREFIXES = (
    ("com.flamePhoenix.plugin", "Flame 外壳和 SDK 插件。"),
    ("com.sqwan", "SQwan 渠道 SDK。"),
    ("com.eyugame", "EyuGame 插件/下载壳。"),
    ("com.sy.frxxz", "游戏包名/R 资源命名空间。"),
    ("com.unity3d.player", "Unity Android Player。"),
    ("com.mobile.auth", "手机号一键登录 SDK。"),
    ("com.tencent.mm", "微信 SDK。"),
    ("com.social.sdk", "社交登录 SDK 抽象层。"),
    ("com.alipay", "支付宝 SDK。"),
    ("com.unionpay", "银联 SDK。"),
)

_DEX_LOGIN_ZERO_CHECK_TERMS = ("taptap", "tapdb", "xindong", "akbing", "prod-login", "cdn-frxxz", "mobi37")


def _dex_login_surface_matches(text: str) -> list[tuple[str, str, int, str]]:
    lower = text.lower()
    matches: list[tuple[str, str, int, str]] = []
    for category, terms, confidence, note in _DEX_LOGIN_SURFACE_TERM_SPECS:
        for term in terms:
            if term.lower() in lower:
                matches.append((category, term, confidence, note))
    return matches


def _iter_dex_login_index_values(output_dir: Path) -> Iterable[dict[str, str]]:
    specs = (
        ("dex_strings.tsv", "string", "value"),
        ("dex_classes.tsv", "class", "java_name"),
        ("dex_methods.tsv", "method", "qualified_name"),
    )
    for table, kind, value_field in specs:
        for row in _read_tsv_rows(output_dir / table):
            value = row.get(value_field, "")
            if not value:
                continue
            yield {
                "table": table,
                "kind": kind,
                "source": f"{row.get('dex', '')}:{row.get('index', '')}",
                "value": value,
            }


def _collect_dex_login_surface_hits(output_dir: Path, *, max_rows: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    zero_counts = Counter({term: 0 for term in _DEX_LOGIN_ZERO_CHECK_TERMS})
    category_counts: Counter[str] = Counter()
    seen: set[tuple[str, str, str, str]] = set()

    for item in _iter_dex_login_index_values(output_dir):
        value = item["value"]
        lower = value.lower()
        for term in _DEX_LOGIN_ZERO_CHECK_TERMS:
            if term.lower() in lower:
                zero_counts[term] += 1
        if len(rows) >= max_rows:
            continue
        for category, matched_term, confidence, note in _dex_login_surface_matches(value):
            if category_counts[category] >= _DEX_LOGIN_SURFACE_CATEGORY_LIMITS.get(category, 50):
                continue
            key = (item["kind"], item["source"], category, value)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "kind": item["kind"],
                    "category": category,
                    "confidence": confidence,
                    "source": item["source"],
                    "matched_term": matched_term,
                    "value": value,
                    "note": note,
                }
            )
            category_counts[category] += 1
            if len(rows) >= max_rows:
                break

    rows.sort(
        key=lambda row: (
            _DEX_LOGIN_SURFACE_CATEGORY_ORDER.get(str(row.get("category", "")), 99),
            -int(row.get("confidence", 0)),
            str(row.get("kind", "")),
            str(row.get("value", "")),
        )
    )
    zero_rows = [
        {
            "term": term,
            "count": zero_counts[term],
            "note": "0 表示该关键词未出现在当前 DEX 字符串/类名/方法名索引中。",
        }
        for term in _DEX_LOGIN_ZERO_CHECK_TERMS
    ]
    return rows, zero_rows


def _collect_dex_login_package_rows(output_dir: Path) -> list[dict[str, object]]:
    class_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    notes = dict(_DEX_LOGIN_PACKAGE_PREFIXES)

    def prefix_for(value: str) -> str:
        lower = value.lower()
        for prefix, _note in _DEX_LOGIN_PACKAGE_PREFIXES:
            if lower.startswith(prefix.lower()):
                return prefix
        return ""

    for row in _read_tsv_rows(output_dir / "dex_classes.tsv"):
        java_name = row.get("java_name", "")
        prefix = prefix_for(java_name)
        if not prefix:
            continue
        class_counts[prefix] += 1
        if len(samples[prefix]) < 5:
            samples[prefix].append(java_name)

    for row in _read_tsv_rows(output_dir / "dex_methods.tsv"):
        class_name = row.get("class_name", "")
        prefix = prefix_for(class_name)
        if prefix:
            method_counts[prefix] += 1

    rows = [
        {
            "prefix": prefix,
            "class_count": class_counts[prefix],
            "method_count": method_counts[prefix],
            "sample_classes": " | ".join(samples.get(prefix, [])),
            "note": notes.get(prefix, ""),
        }
        for prefix in sorted(set(class_counts) | set(method_counts))
    ]
    rows.sort(key=lambda row: (-int(row["class_count"]) - int(row["method_count"]), str(row["prefix"])))
    return rows


def _collect_dex_login_manifest_rows(output_dir: Path, *, max_rows: int = 80) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    component_terms = ("flame", "unity", "sqwan", "wxapi", "wechat", "weixin", "alipay", "tencent", "social", "login")
    intent_terms = ("main", "launcher", "sq", "wx", "weixin", "qq", "oauth")

    for row in _read_tsv_rows(output_dir / "apk_manifest_components.tsv"):
        name = row.get("name", "")
        role = row.get("role", "")
        exported = row.get("exported", "")
        haystack = f"{name} {role}".lower()
        if role == "game-launcher" or exported == "true" or any(term in haystack for term in component_terms):
            rows.append(
                {
                    "kind": "component",
                    "component_type": row.get("type", ""),
                    "component": name,
                    "role_or_intent": role,
                    "value": f"exported={exported}; process={row.get('process', '')}; permission={row.get('permission', '')}",
                    "note": "Manifest 组件入口；是否执行登录逻辑仍需方法体或运行时验证。",
                }
            )
            if len(rows) >= max_rows:
                return rows

    for row in _read_tsv_rows(output_dir / "apk_manifest_intents.tsv"):
        value = row.get("value", "")
        if any(term in value.lower() for term in intent_terms):
            rows.append(
                {
                    "kind": "intent",
                    "component_type": row.get("component_type", ""),
                    "component": row.get("component", ""),
                    "role_or_intent": row.get("kind", ""),
                    "value": value,
                    "note": "Manifest intent/data 入口。",
                }
            )
            if len(rows) >= max_rows:
                return rows
    return rows


def _write_dex_login_surface_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    hit_rows: list[dict[str, object]],
    package_rows: list[dict[str, object]],
    manifest_rows: list[dict[str, object]],
    zero_rows: list[dict[str, object]],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in hit_rows)
    zero_summary = ", ".join(f"{row['term']}={row['count']}" for row in zero_rows)
    lines = [
        "# 凡修 DEX 登录外壳面探针",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告只读 DEX 字符串、类名、方法名和 Manifest；不反编译方法体，不访问线上服务器。",
        "",
        "## 结论",
        "",
        f"- DEX 登录/SDK 命中：{len(hit_rows)} 条；分类分布："
        + ", ".join(f"{key}:{value}" for key, value in category_counts.most_common()),
        f"- 重点零命中检查：{zero_summary}。这说明当前游戏服域名和 TapTap 明文不在 Java/Dex 面，登录服域名仍主要来自 Lua/配置与 IL2CPP bridge。",
        "- Java/Dex 面更像渠道壳和 SDK 聚合层：FlameUnityActivity/SDKPlugin、SQwan、EyuGame、社交 OAuth、手机号一键登录和支付 SDK 都可见。",
        "- 当前边界：DEX 索引没有方法体控制流，不能证明 SDKLogin 如何把 token 传入 Unity/Lua；若要继续看 Java 函数体，需要 Jadx/smali 级反编译。",
        "",
        "## 重点包名聚合",
        "",
        "| prefix | classes | methods | note | sample_classes |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for row in package_rows[:40]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('prefix', ''))}` | "
            f"{_markdown_table_cell(row.get('class_count', ''))} | "
            f"{_markdown_table_cell(row.get('method_count', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} | "
            f"{_markdown_table_cell(row.get('sample_classes', ''))} |"
        )

    lines.extend(["", "## Manifest 入口", "", "| kind | type | component | role/intent | value | note |", "| --- | --- | --- | --- | --- | --- |"])
    for row in manifest_rows[:80]:
        lines.append(
            f"| {_markdown_table_cell(row.get('kind', ''))} | "
            f"{_markdown_table_cell(row.get('component_type', ''))} | "
            f"`{_markdown_table_cell(row.get('component', ''))}` | "
            f"{_markdown_table_cell(row.get('role_or_intent', ''))} | "
            f"{_markdown_table_cell(row.get('value', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(["", "## DEX 命中明细", "", "| category | kind | source | term | value | note |", "| --- | --- | --- | --- | --- | --- |"])
    for row in hit_rows[:220]:
        lines.append(
            f"| {_markdown_table_cell(row.get('category', ''))} | "
            f"{_markdown_table_cell(row.get('kind', ''))} | "
            f"{_markdown_table_cell(row.get('source', ''))} | "
            f"{_markdown_table_cell(row.get('matched_term', ''))} | "
            f"`{_markdown_table_cell(row.get('value', ''))}` | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(["", "## 零命中检查", "", "| term | count | note |", "| --- | ---: | --- |"])
    for row in zero_rows:
        lines.append(
            f"| `{_markdown_table_cell(row.get('term', ''))}` | "
            f"{_markdown_table_cell(row.get('count', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_dex_login_surface_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    max_rows: int = 600,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dex_required = (output_dir / "dex_strings.tsv", output_dir / "dex_classes.tsv", output_dir / "dex_methods.tsv")
    if not all(path.is_file() for path in dex_required):
        build_fanxiu_apk_static_index(apk_root=root, export_root=export_base, keyword_hit_limit=60000)
    build_fanxiu_apk_manifest_probe(apk_root=root, export_root=export_base)

    hit_rows, zero_rows = _collect_dex_login_surface_hits(output_dir, max_rows=max_rows)
    package_rows = _collect_dex_login_package_rows(output_dir)
    manifest_rows = _collect_dex_login_manifest_rows(output_dir)
    category_counts = Counter(str(row.get("category", "")) for row in hit_rows)

    _write_tsv(
        output_dir / "apk_dex_login_surface_hits.tsv",
        ["kind", "category", "confidence", "source", "matched_term", "value", "note"],
        hit_rows,
    )
    _write_tsv(
        output_dir / "apk_dex_login_surface_packages.tsv",
        ["prefix", "class_count", "method_count", "sample_classes", "note"],
        package_rows,
    )
    _write_tsv(
        output_dir / "apk_dex_login_surface_manifest.tsv",
        ["kind", "component_type", "component", "role_or_intent", "value", "note"],
        manifest_rows,
    )
    _write_tsv(output_dir / "apk_dex_login_surface_zero_terms.tsv", ["term", "count", "note"], zero_rows)

    _write_dex_login_surface_markdown(
        output_dir / "apk_dex_login_surface_report.md",
        root=root,
        output_dir=output_dir,
        hit_rows=hit_rows,
        package_rows=package_rows,
        manifest_rows=manifest_rows,
        zero_rows=zero_rows,
    )

    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "hits": len(hit_rows),
            "packages": len(package_rows),
            "manifest_rows": len(manifest_rows),
            "categories": dict(category_counts),
            "zero_terms": {str(row["term"]): int(row["count"]) for row in zero_rows},
        },
        "outputs": {
            "summary": str(output_dir / "apk_dex_login_surface_report.json"),
            "markdown": str(output_dir / "apk_dex_login_surface_report.md"),
            "hits": str(output_dir / "apk_dex_login_surface_hits.tsv"),
            "packages": str(output_dir / "apk_dex_login_surface_packages.tsv"),
            "manifest": str(output_dir / "apk_dex_login_surface_manifest.tsv"),
            "zero_terms": str(output_dir / "apk_dex_login_surface_zero_terms.tsv"),
        },
    }
    (output_dir / "apk_dex_login_surface_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _build_dex_instruction_widths() -> dict[int, int]:
    widths = {opcode: 1 for opcode in range(256)}
    two_units = [
        0x02,
        0x05,
        0x08,
        0x13,
        0x15,
        0x16,
        0x19,
        0x1A,
        0x1C,
        0x1F,
        0x20,
        0x22,
        0x23,
        *range(0x2D, 0x3E),
        *range(0x44, 0x6E),
        *range(0xD0, 0xE3),
        0xFE,
        0xFF,
    ]
    three_units = [
        0x03,
        0x06,
        0x09,
        0x14,
        0x17,
        0x1B,
        0x24,
        0x25,
        0x26,
        0x2A,
        0x2B,
        0x2C,
        *range(0x6E, 0x73),
        *range(0x74, 0x79),
        0xFC,
        0xFD,
    ]
    for opcode in two_units:
        widths[opcode] = 2
    for opcode in three_units:
        widths[opcode] = 3
    widths[0x18] = 5
    widths[0xFA] = 4
    widths[0xFB] = 4
    return widths


_DEX_INSTRUCTION_WIDTHS = _build_dex_instruction_widths()

_DEX_LOGIN_BODY_SEED_TERMS = (
    "FlameUnityActivity.SDKLogin",
    "FlameUnityActivity.ListenBackToGameLogin",
    "SDKCallback.OnSDKLoginData",
    "SDKCallback.OnSDKCancelLogin",
    "SDKCallback.SendDataToUnity",
    "SDKPlugin.SDKLogin",
    "SQwanCore.login",
    "PluginSQwanCore.login",
    "setBackToGameLoginListener",
)

_DEX_LOGIN_BODY_DIRECT_METHODS = {
    "com.flamePhoenix.plugin.activity.FlameUnityActivity.SDKLogin",
    "com.flamePhoenix.plugin.activity.FlameUnityActivity.ListenBackToGameLogin",
    "com.flamePhoenix.plugin.plugin.SDKCallback.OnSDKLoginData",
    "com.flamePhoenix.plugin.plugin.SDKCallback.OnSDKCancelLogin",
    "com.flamePhoenix.plugin.plugin.SDKCallback.SendDataToUnity",
    "com.flamePhoenix.plugin.plugin.SDKPlugin.SDKLogin",
}

_DEX_LOGIN_BODY_REF_TERMS = (
    "SDKLogin",
    "OnSDKLoginData",
    "OnReceiveLogin",
    "SendDataToUnity",
    "UnitySendMessage",
    "GameEnter",
    "SQwanCore.login",
    "token",
    "gid",
    "pid",
    "BackToGameLogin",
)


def _dex_instruction_width(insns: list[int], index: int) -> int:
    code_unit = insns[index]
    opcode = code_unit & 0xFF
    payload_kind = code_unit >> 8
    if opcode == 0 and payload_kind == 1 and index + 1 < len(insns):
        return 4 + 2 * insns[index + 1]
    if opcode == 0 and payload_kind == 2 and index + 1 < len(insns):
        return 2 + 4 * insns[index + 1]
    if opcode == 0 and payload_kind == 3 and index + 3 < len(insns):
        element_width = insns[index + 1]
        size = insns[index + 2] | (insns[index + 3] << 16)
        return 4 + ((element_width * size + 1) // 2)
    return _DEX_INSTRUCTION_WIDTHS.get(opcode, 1)


def _decode_dex_instruction_refs(
    insns: list[int],
    *,
    strings: list[str],
    types: list[str],
    fields: list[str],
    methods: list[dict[str, object]],
) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    cursor = 0
    ordinal = 0
    while cursor < len(insns):
        code_unit = insns[cursor]
        opcode = code_unit & 0xFF
        ref_kind = ""
        ref_index = -1
        ref_value = ""
        if opcode == 0x1A and cursor + 1 < len(insns):
            ref_kind = "string"
            ref_index = insns[cursor + 1]
            ref_value = _get_seq(strings, ref_index)
        elif opcode == 0x1B and cursor + 2 < len(insns):
            ref_kind = "string"
            ref_index = insns[cursor + 1] | (insns[cursor + 2] << 16)
            ref_value = _get_seq(strings, ref_index)
        elif opcode in {0x1C, 0x1F, 0x20, 0x22, 0x23} and cursor + 1 < len(insns):
            ref_kind = "type"
            ref_index = insns[cursor + 1]
            ref_value = _descriptor_to_java_name(_get_seq(types, ref_index))
        elif 0x52 <= opcode <= 0x6D and cursor + 1 < len(insns):
            ref_kind = "field"
            ref_index = insns[cursor + 1]
            ref_value = _get_seq(fields, ref_index)
        elif (0x6E <= opcode <= 0x72 or 0x74 <= opcode <= 0x78) and cursor + 1 < len(insns):
            ref_kind = "call"
            ref_index = insns[cursor + 1]
            method = methods[ref_index] if 0 <= ref_index < len(methods) else {}
            ref_value = str(method.get("qualified_name", ""))

        if ref_kind:
            refs.append(
                {
                    "ordinal": ordinal,
                    "offset_code_unit": cursor,
                    "opcode": f"0x{opcode:02x}",
                    "ref_kind": ref_kind,
                    "ref_index": ref_index,
                    "ref_value": ref_value,
                }
            )
            ordinal += 1
        width = _dex_instruction_width(insns, cursor)
        cursor += max(width, 1)
    return refs


def _parse_dex_login_body_detail(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if not data.startswith(b"dex\n"):
        raise FanxiuResourceError(f"不是有效 DEX 文件：{path}")

    string_ids_size = _read_u32(data, 56)
    string_ids_off = _read_u32(data, 60)
    type_ids_size = _read_u32(data, 64)
    type_ids_off = _read_u32(data, 68)
    field_ids_size = _read_u32(data, 80)
    field_ids_off = _read_u32(data, 84)
    method_ids_size = _read_u32(data, 88)
    method_ids_off = _read_u32(data, 92)
    class_defs_size = _read_u32(data, 96)
    class_defs_off = _read_u32(data, 100)

    strings: list[str] = []
    for index in range(string_ids_size):
        string_data_off = _read_u32(data, string_ids_off + index * 4)
        _utf16_size, cursor = _read_uleb128(data, string_data_off)
        end = data.find(b"\x00", cursor)
        if end < 0:
            end = min(len(data), cursor + 4096)
        strings.append(data[cursor:end].decode("utf-8", errors="replace"))

    types = [_get_seq(strings, _read_u32(data, type_ids_off + index * 4)) for index in range(type_ids_size)]

    fields: list[str] = []
    for index in range(field_ids_size):
        class_idx, type_idx, name_idx = struct.unpack_from("<HHI", data, field_ids_off + index * 8)
        owner = _descriptor_to_java_name(_get_seq(types, class_idx))
        field_type = _descriptor_to_java_name(_get_seq(types, type_idx))
        name = _get_seq(strings, name_idx)
        fields.append(f"{owner}.{name}:{field_type}" if field_type else f"{owner}.{name}")

    methods: list[dict[str, object]] = []
    for index in range(method_ids_size):
        class_idx, proto_idx, name_idx = struct.unpack_from("<HHI", data, method_ids_off + index * 8)
        class_name = _descriptor_to_java_name(_get_seq(types, class_idx))
        name = _get_seq(strings, name_idx)
        methods.append(
            {
                "index": index,
                "class_name": class_name,
                "name": name,
                "proto_index": proto_idx,
                "qualified_name": f"{class_name}.{name}" if class_name and name else name,
            }
        )

    code_offsets: dict[int, int] = {}
    for class_index in range(class_defs_size):
        class_def_offset = class_defs_off + class_index * 32
        class_data_off = _read_u32(data, class_def_offset + 24)
        if not class_data_off:
            continue
        cursor = class_data_off
        static_fields_size, cursor = _read_uleb128(data, cursor)
        instance_fields_size, cursor = _read_uleb128(data, cursor)
        direct_methods_size, cursor = _read_uleb128(data, cursor)
        virtual_methods_size, cursor = _read_uleb128(data, cursor)
        for _ in range(static_fields_size + instance_fields_size):
            _field_idx_diff, cursor = _read_uleb128(data, cursor)
            _access_flags, cursor = _read_uleb128(data, cursor)
        for method_count in (direct_methods_size, virtual_methods_size):
            method_index = 0
            for _ in range(method_count):
                method_idx_diff, cursor = _read_uleb128(data, cursor)
                _access_flags, cursor = _read_uleb128(data, cursor)
                code_off, cursor = _read_uleb128(data, cursor)
                method_index += method_idx_diff
                code_offsets[method_index] = code_off

    return {
        "data": data,
        "strings": strings,
        "types": types,
        "fields": fields,
        "methods": methods,
        "code_offsets": code_offsets,
    }


def _dex_method_insns(data: bytes, code_off: int) -> list[int]:
    if not code_off or code_off + 16 > len(data):
        return []
    insns_size = _read_u32(data, code_off + 12)
    start = code_off + 16
    max_size = max((len(data) - start) // 2, 0)
    actual_size = min(insns_size, max_size)
    return [struct.unpack_from("<H", data, start + index * 2)[0] for index in range(actual_size)]


def _dex_login_body_method_role(qualified_name: str) -> str:
    if qualified_name == "com.flamePhoenix.plugin.activity.FlameUnityActivity.SDKLogin":
        return "android_login_entry"
    if qualified_name == "com.flamePhoenix.plugin.activity.FlameUnityActivity.ListenBackToGameLogin":
        return "back_to_login_listener"
    if qualified_name.endswith("FlameUnityActivity$7.onSuccess"):
        return "sdk_login_success_callback"
    if qualified_name.endswith("FlameUnityActivity$7.onFailture") or qualified_name.endswith("FlameUnityActivity$7.onFailure"):
        return "sdk_login_failure_callback"
    if qualified_name.endswith("FlameUnityActivity$4.onSuccess") or qualified_name.endswith("FlameUnityActivity$4.onFailture"):
        return "back_to_login_callback"
    if qualified_name == "com.flamePhoenix.plugin.plugin.SDKCallback.OnSDKLoginData":
        return "unity_login_message_builder"
    if qualified_name == "com.flamePhoenix.plugin.plugin.SDKCallback.SendDataToUnity":
        return "unity_send_message"
    if qualified_name == "com.flamePhoenix.plugin.plugin.SDKPlugin.SDKLogin":
        return "plugin_login_entry"
    if qualified_name.endswith("SDKPlugin$4.run"):
        return "plugin_handler_message"
    if ".SQwanCore.login" in qualified_name or ".PluginSQwanCore.login" in qualified_name:
        return "channel_sdk_login"
    if "setBackToGameLoginListener" in qualified_name:
        return "channel_back_listener"
    return "related_method"


def _dex_login_ref_note(value: str) -> str:
    if value == "token":
        return "登录成功回调从 Bundle 取 token。"
    if value == "gid":
        return "登录成功回调从 Bundle 取 gid。"
    if value == "pid":
        return "登录成功回调从 Bundle 取 pid。"
    if value == "1__":
        return "登录成功数据前缀，后续与 token/gid/pid 拼接。"
    if value == "__":
        return "登录成功数据分隔符。"
    if value == "OnReceiveLogin":
        return "发送给 Unity 的登录消息名。"
    if value == "GameEnter":
        return "UnitySendMessage 的 GameObject 名。"
    if "UnitySendMessage" in value:
        return "Java 到 Unity 的消息桥。"
    if "OnSDKLoginData" in value:
        return "Java SDK 登录成功数据进入 Unity 回传封装。"
    if "SendDataToUnity" in value:
        return "统一 UnitySendMessage 封装。"
    if "SQwanCore.login" in value:
        return "FlameUnityActivity.SDKLogin 下传到 SQwan 渠道登录。"
    return ""


def _collect_dex_login_body_rows(root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    method_rows: list[dict[str, object]] = []
    ref_rows: list[dict[str, object]] = []
    flow_rows: list[dict[str, object]] = []

    for dex_path in sorted(root.glob("classes*.dex"), key=lambda item: (len(item.name), item.name)):
        parsed = _parse_dex_login_body_detail(dex_path)
        data = parsed["data"]
        strings = parsed["strings"]
        types = parsed["types"]
        fields = parsed["fields"]
        methods = parsed["methods"]
        code_offsets = parsed["code_offsets"]

        decoded_cache: dict[int, list[dict[str, object]]] = {}

        def decoded_refs(method_index: int) -> list[dict[str, object]]:
            if method_index in decoded_cache:
                return decoded_cache[method_index]
            code_off = int(code_offsets.get(method_index, 0))
            insns = _dex_method_insns(data, code_off)
            decoded_cache[method_index] = _decode_dex_instruction_refs(
                insns,
                strings=strings,
                types=types,
                fields=fields,
                methods=methods,
            )
            return decoded_cache[method_index]

        selected_indexes: set[int] = set()
        for method in methods:
            qualified_name = str(method.get("qualified_name", ""))
            if qualified_name in _DEX_LOGIN_BODY_DIRECT_METHODS or any(term in qualified_name for term in _DEX_LOGIN_BODY_SEED_TERMS):
                selected_indexes.add(int(method["index"]))

        callback_owners: set[str] = set()
        one_hop_calls: set[str] = set()
        for method_index in list(selected_indexes):
            for ref in decoded_refs(method_index):
                value = str(ref.get("ref_value", ""))
                if ref.get("ref_kind") == "type" and (
                    value.startswith("com.flamePhoenix.plugin.activity.FlameUnityActivity$")
                    or value.startswith("com.flamePhoenix.plugin.plugin.SDKPlugin$")
                ):
                    callback_owners.add(value)
                if ref.get("ref_kind") == "call" and (
                    "SDKCallback." in value
                    or "SQwanCore." in value
                    or "PluginSQwanCore." in value
                    or "UnitySendMessage" in value
                ):
                    one_hop_calls.add(value)

        for method in methods:
            qualified_name = str(method.get("qualified_name", ""))
            class_name = str(method.get("class_name", ""))
            name = str(method.get("name", ""))
            if class_name in callback_owners and name in {"<init>", "onSuccess", "onFailture", "onFailure", "onAction", "run"}:
                selected_indexes.add(int(method["index"]))
            if qualified_name in one_hop_calls:
                selected_indexes.add(int(method["index"]))

        for method_index in sorted(selected_indexes):
            method = methods[method_index]
            qualified_name = str(method.get("qualified_name", ""))
            refs = decoded_refs(method_index)
            code_off = int(code_offsets.get(method_index, 0))
            strings_seen = [str(ref["ref_value"]) for ref in refs if ref.get("ref_kind") == "string"]
            calls_seen = [str(ref["ref_value"]) for ref in refs if ref.get("ref_kind") == "call"]
            type_refs_seen = [str(ref["ref_value"]) for ref in refs if ref.get("ref_kind") == "type"]
            role = _dex_login_body_method_role(qualified_name)
            method_rows.append(
                {
                    "dex": dex_path.name,
                    "method_index": method_index,
                    "role": role,
                    "qualified_name": qualified_name,
                    "code_off": code_off,
                    "insn_count": len(_dex_method_insns(data, code_off)),
                    "ref_count": len(refs),
                    "strings": " | ".join(strings_seen[:12]),
                    "calls": " | ".join(calls_seen[:12]),
                    "type_refs": " | ".join(type_refs_seen[:8]),
                    "note": "DEX code_item 轻量解码，只提取引用，不还原完整 Java 控制流。",
                }
            )
            for ref in refs:
                value = str(ref.get("ref_value", ""))
                interesting = int(any(term.lower() in value.lower() for term in _DEX_LOGIN_BODY_REF_TERMS))
                ref_rows.append(
                    {
                        "dex": dex_path.name,
                        "source_method": qualified_name,
                        "source_role": role,
                        "ordinal": ref.get("ordinal", ""),
                        "offset_code_unit": ref.get("offset_code_unit", ""),
                        "opcode": ref.get("opcode", ""),
                        "ref_kind": ref.get("ref_kind", ""),
                        "ref_index": ref.get("ref_index", ""),
                        "ref_value": value,
                        "interesting": interesting,
                        "note": _dex_login_ref_note(value),
                    }
                )

    def method_refs(method_name: str) -> list[dict[str, object]]:
        return [row for row in ref_rows if row.get("source_method") == method_name]

    def has_ref(method_name: str, value_part: str) -> bool:
        needle = value_part.lower()
        return any(needle in str(row.get("ref_value", "")).lower() for row in method_refs(method_name))

    derived = [
        (
            "01-sdk-login-entry",
            "com.flamePhoenix.plugin.activity.FlameUnityActivity.SDKLogin",
            "com.sqwan.msdk.SQwanCore.login",
            "Activity 登录入口把回调对象交给 SQwan 渠道 SDK 登录。",
        ),
        (
            "02-success-callback",
            "com.flamePhoenix.plugin.activity.FlameUnityActivity$7.onSuccess",
            "Bundle token/gid/pid -> SDKCallback.OnSDKLoginData",
            "登录成功回调读取 token/gid/pid，并拼接为 Unity 回传数据。",
        ),
        (
            "03-unity-login-event",
            "com.flamePhoenix.plugin.plugin.SDKCallback.OnSDKLoginData",
            "OnReceiveLogin -> SendDataToUnity",
            "SDKCallback 选择 Unity 登录消息名 OnReceiveLogin。",
        ),
        (
            "04-unity-bridge",
            "com.flamePhoenix.plugin.plugin.SDKCallback.SendDataToUnity",
            "UnityPlayer.UnitySendMessage(GameEnter, event, data)",
            "最终通过 UnitySendMessage 发到 GameEnter 对象。",
        ),
        (
            "05-back-login",
            "com.flamePhoenix.plugin.activity.FlameUnityActivity.ListenBackToGameLogin",
            "SQwanCore.setBackToGameLoginListener",
            "返回登录监听与取消登录事件同属 Java SDK 外壳面。",
        ),
    ]
    for step, source, target, note in derived:
        confidence = 90 if has_ref(source, target.split(" -> ")[-1].split("(")[0].split()[0]) else 70
        if source.endswith("$7.onSuccess") and all(has_ref(source, item) for item in ("token", "gid", "pid", "OnSDKLoginData")):
            confidence = 95
        if source.endswith("OnSDKLoginData") and has_ref(source, "OnReceiveLogin") and has_ref(source, "SendDataToUnity"):
            confidence = 95
        if source.endswith("SendDataToUnity") and has_ref(source, "UnitySendMessage") and has_ref(source, "GameEnter"):
            confidence = 95
        flow_rows.append({"step": step, "source": source, "target": target, "confidence": confidence, "note": note})

    method_rows.sort(key=lambda row: (str(row.get("dex", "")), str(row.get("role", "")), str(row.get("qualified_name", ""))))
    ref_rows.sort(
        key=lambda row: (
            str(row.get("dex", "")),
            str(row.get("source_method", "")),
            int(str(row.get("ordinal") or "0")),
        )
    )
    return method_rows, ref_rows, flow_rows


def _write_dex_login_body_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    method_rows: list[dict[str, object]],
    ref_rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
) -> None:
    role_counts = Counter(str(row.get("role", "")) for row in method_rows)
    interesting_refs = [row for row in ref_rows if str(row.get("interesting", "")) == "1"]
    lines = [
        "# 凡修 DEX 登录方法体轻量探针",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告直接解析 DEX `class_data/code_item`，只提取 `const-string / invoke / field / type` 引用；不依赖 Jadx，也不还原完整 Java 控制流。",
        "",
        "## 结论",
        "",
        f"- 目标方法：{len(method_rows)} 个；角色分布："
        + ", ".join(f"{key}:{value}" for key, value in role_counts.most_common()),
        f"- 目标引用：{len(ref_rows)} 条，其中关键引用 {len(interesting_refs)} 条。",
        "- 登录链路已可静态串起：`FlameUnityActivity.SDKLogin -> SQwanCore.login -> FlameUnityActivity$7.onSuccess -> SDKCallback.OnSDKLoginData -> SendDataToUnity -> UnityPlayer.UnitySendMessage(GameEnter, OnReceiveLogin, data)`。",
        "- 成功回调中可见 `token/gid/pid`，并用 `1__` 与 `__` 拼成回传字符串；这说明 Java 渠道登录 token 是先回 Unity，再由 Unity/Lua 后续走登录服校验。",
        "- 当前边界：轻量 DEX 解码已能确认引用链，但看不到分支条件、异常边、参数寄存器精确流向；完整 Java 源级阅读仍需要 Jadx/smali。",
        "",
        "## 推导链路",
        "",
        "| step | source | target | confidence | note |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''))}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(["", "## 方法摘要", "", "| role | dex | method | code_off | refs | strings | calls |", "| --- | --- | --- | ---: | ---: | --- | --- |"])
    for row in method_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('role', ''))} | "
            f"{_markdown_table_cell(row.get('dex', ''))} | "
            f"`{_markdown_table_cell(row.get('qualified_name', ''))}` | "
            f"{_markdown_table_cell(row.get('code_off', ''))} | "
            f"{_markdown_table_cell(row.get('ref_count', ''))} | "
            f"{_markdown_table_cell(row.get('strings', ''))} | "
            f"{_markdown_table_cell(row.get('calls', ''))} |"
        )

    lines.extend(["", "## 关键引用", "", "| source | kind | value | note |", "| --- | --- | --- | --- |"])
    for row in interesting_refs[:120]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('source_method', ''))}` | "
            f"{_markdown_table_cell(row.get('ref_kind', ''))} | "
            f"`{_markdown_table_cell(row.get('ref_value', ''))}` | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_dex_login_body_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    method_rows, ref_rows, flow_rows = _collect_dex_login_body_rows(root)
    role_counts = Counter(str(row.get("role", "")) for row in method_rows)
    interesting_count = sum(1 for row in ref_rows if int(row.get("interesting", 0)))

    _write_tsv(
        output_dir / "apk_dex_login_body_methods.tsv",
        ["dex", "method_index", "role", "qualified_name", "code_off", "insn_count", "ref_count", "strings", "calls", "type_refs", "note"],
        method_rows,
    )
    _write_tsv(
        output_dir / "apk_dex_login_body_refs.tsv",
        [
            "dex",
            "source_method",
            "source_role",
            "ordinal",
            "offset_code_unit",
            "opcode",
            "ref_kind",
            "ref_index",
            "ref_value",
            "interesting",
            "note",
        ],
        ref_rows,
    )
    _write_tsv(output_dir / "apk_dex_login_body_flow.tsv", ["step", "source", "target", "confidence", "note"], flow_rows)
    _write_dex_login_body_markdown(
        output_dir / "apk_dex_login_body_report.md",
        root=root,
        output_dir=output_dir,
        method_rows=method_rows,
        ref_rows=ref_rows,
        flow_rows=flow_rows,
    )

    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "methods": len(method_rows),
            "refs": len(ref_rows),
            "interesting_refs": interesting_count,
            "flows": len(flow_rows),
            "roles": dict(role_counts),
        },
        "outputs": {
            "summary": str(output_dir / "apk_dex_login_body_report.json"),
            "markdown": str(output_dir / "apk_dex_login_body_report.md"),
            "methods": str(output_dir / "apk_dex_login_body_methods.tsv"),
            "refs": str(output_dir / "apk_dex_login_body_refs.tsv"),
            "flow": str(output_dir / "apk_dex_login_body_flow.tsv"),
        },
    }
    (output_dir / "apk_dex_login_body_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _dex_login_refs_for_method(ref_rows: list[dict[str, str]], method_name: str) -> list[dict[str, str]]:
    rows = [row for row in ref_rows if row.get("source_method") == method_name]
    return sorted(rows, key=lambda row: int(str(row.get("ordinal") or "0")))


def _dex_login_ref_offsets(rows: list[dict[str, str]], needle: str, *, exact: bool = False) -> str:
    offsets = [
        str(row.get("offset_code_unit", ""))
        for row in rows
        if (str(row.get("ref_value", "")) == needle if exact else needle in str(row.get("ref_value", "")))
    ]
    return "、".join(offset for offset in offsets if offset)


def _collect_dex_login_payload_shape_rows(output_dir: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    ref_path = output_dir / "apk_dex_login_body_refs.tsv"
    if not ref_path.is_file():
        raise FanxiuResourceError("缺少 apk_dex_login_body_refs.tsv，请先运行 dex-login-body-probe。")

    ref_rows = _read_tsv_rows(ref_path)
    success_method = "com.flamePhoenix.plugin.activity.FlameUnityActivity$7.onSuccess"
    event_method = "com.flamePhoenix.plugin.plugin.SDKCallback.OnSDKLoginData"
    send_method = "com.flamePhoenix.plugin.plugin.SDKCallback.SendDataToUnity"
    success_refs = _dex_login_refs_for_method(ref_rows, success_method)
    event_refs = _dex_login_refs_for_method(ref_rows, event_method)
    send_refs = _dex_login_refs_for_method(ref_rows, send_method)

    success_values = [str(row.get("ref_value", "")) for row in success_refs]
    event_values = [str(row.get("ref_value", "")) for row in event_refs]
    send_values = [str(row.get("ref_value", "")) for row in send_refs]
    has_inputs = all(value in success_values for value in ("token", "gid", "pid"))
    has_payload_literals = "1__" in success_values and "__" in success_values
    has_payload_fields = any("m_gid" in value for value in success_values) and any("m_pid" in value for value in success_values)
    has_unity_call = any("OnSDKLoginData" in value for value in success_values)
    has_event = "OnReceiveLogin" in event_values
    has_game_object = "GameEnter" in send_values
    has_unity_send = any("UnitySendMessage" in value for value in send_values)
    payload_template = "1__{token}__{gid}__{pid}" if has_inputs and has_payload_literals and has_payload_fields else ""
    confidence = 95 if payload_template and has_unity_call and has_event and has_game_object and has_unity_send else 70

    rows: list[dict[str, object]] = [
        {
            "stage": "sdk_success_bundle_inputs",
            "owner": success_method,
            "artifact": "Bundle.getString keys",
            "value": "token/gid/pid" if has_inputs else "",
            "confidence": 95 if has_inputs else 40,
            "evidence": "token@"
            + _dex_login_ref_offsets(success_refs, "token", exact=True)
            + "; gid@"
            + _dex_login_ref_offsets(success_refs, "gid", exact=True)
            + "; pid@"
            + _dex_login_ref_offsets(success_refs, "pid", exact=True),
            "interpretation": "Java 渠道 SDK 成功回调从 Bundle 读取 token、gid、pid。",
        },
        {
            "stage": "unity_payload_template",
            "owner": success_method,
            "artifact": "StringBuilder payload",
            "value": payload_template,
            "confidence": confidence,
            "evidence": "1__@"
            + _dex_login_ref_offsets(success_refs, "1__", exact=True)
            + "; __@"
            + _dex_login_ref_offsets(success_refs, "__", exact=True)
            + "; m_gid@"
            + _dex_login_ref_offsets(success_refs, "m_gid")
            + "; m_pid@"
            + _dex_login_ref_offsets(success_refs, "m_pid"),
            "interpretation": "静态引用顺序和字段读取显示登录成功 payload 形状为状态码、token、gid、pid 四段。",
        },
        {
            "stage": "unity_login_event",
            "owner": event_method,
            "artifact": "Unity event name",
            "value": "OnReceiveLogin" if has_event else "",
            "confidence": 95 if has_event else 40,
            "evidence": "OnReceiveLogin@" + _dex_login_ref_offsets(event_refs, "OnReceiveLogin"),
            "interpretation": "SDKCallback.OnSDKLoginData 将 payload 包装为 Unity 登录事件。",
        },
        {
            "stage": "unity_send_target",
            "owner": send_method,
            "artifact": "UnitySendMessage target",
            "value": "GameEnter.OnReceiveLogin(data)" if has_game_object and has_unity_send else "",
            "confidence": 95 if has_game_object and has_unity_send else 40,
            "evidence": "GameEnter@"
            + _dex_login_ref_offsets(send_refs, "GameEnter", exact=True)
            + "; UnitySendMessage@"
            + _dex_login_ref_offsets(send_refs, "UnitySendMessage"),
            "interpretation": "Java 层最终通过 UnityPlayer.UnitySendMessage 发到 GameEnter 对象。",
        },
        {
            "stage": "privacy_boundary",
            "owner": "static-analysis-report",
            "artifact": "payload redaction policy",
            "value": "template-only",
            "confidence": 100,
            "evidence": "no runtime token values are read or persisted",
            "interpretation": "本探针只记录字段名和模板，不采集真实 token/gid/pid 值。",
        },
    ]
    verdict = {
        "bundle_inputs_recovered": has_inputs,
        "payload_template_recovered": payload_template == "1__{token}__{gid}__{pid}",
        "unity_event_recovered": has_event,
        "unity_send_target_recovered": has_game_object and has_unity_send,
        "persists_secret_values": False,
        "needs_jadx_for_branch_conditions": True,
    }
    return rows, verdict


def _write_dex_login_payload_shape_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    rows: list[dict[str, object]],
    verdict: dict[str, object],
) -> None:
    template = next((str(row.get("value", "")) for row in rows if row.get("stage") == "unity_payload_template"), "")
    lines = [
        "# 凡修 DEX 登录 Payload 形状探针",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告基于 `apk_dex_login_body_refs.tsv` 的 DEX code_item 引用顺序归纳登录成功回灌模板；不读取、不保存任何真实账号 token。",
        "",
        "## 结论",
        "",
        f"- Java SDK 登录成功 payload 模板：`{template or 'unknown'}`。",
        "- 链路：`FlameUnityActivity$7.onSuccess -> SDKCallback.OnSDKLoginData -> SendDataToUnity -> UnityPlayer.UnitySendMessage(GameEnter, OnReceiveLogin, data)`。",
        "- `token/gid/pid` 来自渠道 SDK 成功回调 `Bundle.getString(...)`；后续 Unity/Lua 再用该 payload 做登录服校验。",
        "- 当前仍不还原完整 Java 分支/异常控制流；若要读源级分支，可再引入 Jadx/smali。",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")
    lines.extend(
        [
            "",
            "## Evidence Rows",
            "",
            "| stage | owner | artifact | value | confidence | evidence | interpretation |",
            "| --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('stage', ''))} | "
            f"`{_markdown_table_cell(row.get('owner', ''))}` | "
            f"{_markdown_table_cell(row.get('artifact', ''))} | "
            f"`{_markdown_table_cell(row.get('value', ''))}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('evidence', ''))} | "
            f"{_markdown_table_cell(row.get('interpretation', ''))} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_dex_login_payload_shape_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not (output_dir / "apk_dex_login_body_refs.tsv").is_file():
        build_fanxiu_apk_dex_login_body_probe(apk_root=root, export_root=export_base)

    rows, verdict = _collect_dex_login_payload_shape_rows(output_dir)
    _write_tsv(
        output_dir / "apk_dex_login_payload_shape.tsv",
        ["stage", "owner", "artifact", "value", "confidence", "evidence", "interpretation"],
        rows,
    )
    _write_dex_login_payload_shape_markdown(
        output_dir / "apk_dex_login_payload_shape_report.md",
        root=root,
        output_dir=output_dir,
        rows=rows,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "rows": len(rows),
        },
        "verdict": verdict,
        "outputs": {
            "rows": str(output_dir / "apk_dex_login_payload_shape.tsv"),
            "markdown": str(output_dir / "apk_dex_login_payload_shape_report.md"),
            "json": str(output_dir / "apk_dex_login_payload_shape_report.json"),
        },
    }
    (output_dir / "apk_dex_login_payload_shape_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_NORES_DIR_NAME = "jadx_1_5_5_nores"
_JADX_PORTABLE_JAVA_EXE = Path(r"D:\tools\Java\temurin17-jre-17.0.19_10\bin\java.exe")
_JADX_PORTABLE_EXE = Path(r"D:\tools\jadx\1.5.5\bin\jadx.bat")
_JADX_DECOMPILE_COMMAND = (
    "jadx -d <export_root>\\apk_static_index\\jadx_1_5_5_nores "
    "--no-res --show-bad-code --threads-count 8 <apk>"
)

_JADX_LOGIN_SOURCE_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("sdk_init_gid", "appConfig.getGameid()", "渠道初始化读取 game id。"),
    ("sdk_init_pid", "appConfig.getPartner()", "渠道初始化读取 partner/pid。"),
    ("switch_token_read", 'bundle.getString("token")', "切号/登录成功回调读取渠道 token。"),
    ("bundle_gid_read", 'bundle.getString("gid")', "渠道回调读取 gid。"),
    ("bundle_pid_read", "bundle.getString(PushConsts.KEY_SERVICE_PIT)", "渠道回调读取 pid；JADX 常量名来自个推 PushConsts。"),
    ("sdk_login_call", "SQwanCore.getInstance().login", "FlameUnityActivity.SDKLogin 进入 SQwan 登录。"),
    ("login_payload_success", 'SDKCallback.OnSDKLoginData("1__"', "登录成功 payload 模板入口。"),
    ("login_payload_failure", 'SDKCallback.OnSDKLoginData("0__"', "登录失败 payload 模板入口。"),
    ("switch_payload_success", 'SDKCallback.OnSDKSwitchAccount("1__"', "切号成功 payload 模板入口。"),
    ("change_payload_success", 'SDKCallback.OnSDKChangeAccount("1__"', "换号成功 payload 模板入口。"),
    ("sdk_callback_login_event", 'SendDataToUnity("OnReceiveLogin"', "SDKCallback 把登录 payload 包装为 Unity 登录事件。"),
    ("sdk_callback_switch_event", 'SendDataToUnity("OnReceiveSwitchAccount"', "SDKCallback 把切号 payload 包装为 Unity 切号事件。"),
    ("sdk_callback_change_event", 'SendDataToUnity("OnReceiveChangeAccount"', "SDKCallback 把换号 payload 包装为 Unity 换号事件。"),
    ("sdk_callback_cancel_event", 'SendDataToUnity("OnReceiveCancelLogin"', "SDKCallback 把取消登录包装为 Unity 事件。"),
    ("unity_game_enter_send", 'UnityPlayer.UnitySendMessage("GameEnter"', "Java 层发给 Unity 的 GameObject 名为 GameEnter。"),
    ("unity_player_send_api", "public static void UnitySendMessage", "UnityPlayer Java API 边界。"),
    ("unity_native_send_boundary", "nativeUnitySendMessage", "Unity Java 层进入 native Unity 消息边界。"),
    ("sqwan_core_login_wrapper", "public void login(Context context", "SQwanCore facade 的登录入口。"),
)


def _fanxiu_jadx_sources_root(export_base: Path) -> Path:
    return (export_base / "apk_static_index" / _JADX_NORES_DIR_NAME / "sources").resolve()


def _collect_jadx_login_source_hits(sources_root: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 JADX 源码目录：{sources_root}。请先用 portable JADX 对 APK 运行 no-res 反编译。"
        )

    java_files = sorted(sources_root.rglob("*.java"))
    rows: list[dict[str, object]] = []
    error_marker_files: set[str] = set()
    error_marker_lines = 0
    hit_counts: Counter[str] = Counter()

    for java_path in java_files:
        rel_path = java_path.relative_to(sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if "JADX ERROR" in clean_line:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            for category, needle, interpretation in _JADX_LOGIN_SOURCE_NEEDLES:
                if needle not in clean_line:
                    continue
                hit_counts[category] += 1
                rows.append(
                    {
                        "category": category,
                        "file": rel_path,
                        "line": line_no,
                        "needle": needle,
                        "text": clean_line,
                        "interpretation": interpretation,
                    }
                )

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    verdict: dict[str, object] = {
        "jadx_sources_present": len(java_files) > 0,
        "login_success_payload_template_found": has("login_payload_success"),
        "login_failure_payload_template_found": has("login_payload_failure"),
        "bundle_token_gid_pid_reads_found": has("switch_token_read") and has("bundle_gid_read") and has("bundle_pid_read"),
        "unity_login_callback_route_found": has("sdk_callback_login_event") and has("unity_game_enter_send"),
        "unity_native_boundary_found": has("unity_player_send_api") and has("unity_native_send_boundary"),
        "switch_and_change_account_paths_found": has("switch_payload_success") and has("change_payload_success"),
        "java_confirms_dex_payload_shape": has("login_payload_success")
        and has("sdk_callback_login_event")
        and has("unity_game_enter_send"),
        "decompile_has_error_markers": bool(error_marker_files),
        "safe_static_template_only": True,
    }
    stats: dict[str, object] = {
        "java_source_count": len(java_files),
        "hit_rows": len(rows),
        "hit_categories": dict(hit_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    rows.sort(key=lambda row: (str(row["file"]), int(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_login_source_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    confidence = 95 if verdict.get("java_confirms_dex_payload_shape") else 65
    return [
        {
            "step": "01-sdk-login",
            "source": "FlameUnityActivity.SDKLogin",
            "target": "SQwanCore.getInstance().login(context, SQResultListener)",
            "confidence": confidence,
            "note": "JADX Java source exposes the readable SDK login call site.",
        },
        {
            "step": "02-success-bundle",
            "source": "SQResultListener.onSuccess(Bundle)",
            "target": "token/gid/pid from Bundle.getString(...)",
            "confidence": 95 if verdict.get("bundle_token_gid_pid_reads_found") else 50,
            "note": "Only field names and template shape are recorded; runtime token values are not collected.",
        },
        {
            "step": "03-payload-template",
            "source": "FlameUnityActivity$7.onSuccess",
            "target": "SDKCallback.OnSDKLoginData(\"1__\" + token + \"__\" + gid + \"__\" + pid)",
            "confidence": 95 if verdict.get("login_success_payload_template_found") else 50,
            "note": "This is the same payload shape inferred earlier from DEX refs, now confirmed in readable Java.",
        },
        {
            "step": "04-unity-event",
            "source": "SDKCallback.OnSDKLoginData",
            "target": "SendDataToUnity(\"OnReceiveLogin\", payload)",
            "confidence": 95 if verdict.get("unity_login_callback_route_found") else 50,
            "note": "SDKCallback maps Java login data onto the Unity message name.",
        },
        {
            "step": "05-unity-native-boundary",
            "source": "SDKCallback.SendDataToUnity",
            "target": "UnityPlayer.UnitySendMessage(\"GameEnter\", method, data)",
            "confidence": 95 if verdict.get("unity_native_boundary_found") else 60,
            "note": "JADX also shows UnityPlayer's Java API and nativeUnitySendMessage boundary.",
        },
    ]


def _write_jadx_login_source_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX login source report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- JADX sources: `{sources_root}`",
        f"- Portable Java: `{_JADX_PORTABLE_JAVA_EXE}`",
        f"- Portable JADX: `{_JADX_PORTABLE_EXE}`",
        f"- Decompile command shape: `{_JADX_DECOMPILE_COMMAND}`",
        "- Scope: static Java source evidence only. This report records payload templates and call names, not live token values.",
        "",
        "## Conclusion",
        "",
        "- JADX confirms the login success payload shape as `1__{token}__{gid}__{pid}` in readable Java source.",
        "- The confirmed route is `FlameUnityActivity.SDKLogin -> SQwanCore.login -> SQResultListener.onSuccess -> SDKCallback.OnSDKLoginData -> SendDataToUnity(\"OnReceiveLogin\") -> UnityPlayer.UnitySendMessage(\"GameEnter\", ...)`.",
        "- Switch-account and change-account success routes use the same `1__token__gid__pid` family; login failure uses the `0__error` family.",
        "- Some third-party SDK files still contain JADX error markers, but the Fanxiu login shell files needed for this report are readable.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| java_source_count | {stats.get('java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=360)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(
        [
            "",
            "## Hit Categories",
            "",
            "| category | count |",
            "| --- | ---: |",
        ]
    )
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| category | source | line | text | interpretation |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:160]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''))}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_login_source_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sources_root = _fanxiu_jadx_sources_root(export_base)

    rows, meta = _collect_jadx_login_source_hits(sources_root)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_login_source_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_login_source_hits.tsv",
        ["category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_login_source_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_login_source_markdown(
        output_dir / "apk_jadx_login_source_report.md",
        root=root,
        export_base=export_base,
        sources_root=sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "sources_root": str(sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tools": {
            "java": str(_JADX_PORTABLE_JAVA_EXE),
            "jadx": str(_JADX_PORTABLE_EXE),
            "decompile_command": _JADX_DECOMPILE_COMMAND,
        },
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_login_source_report.md"),
            "hits": str(output_dir / "apk_jadx_login_source_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_login_source_flow.tsv"),
            "json": str(output_dir / "apk_jadx_login_source_report.json"),
        },
    }
    (output_dir / "apk_jadx_login_source_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_SQ_PLUGIN_DIR_NAME = "jadx_sq_plugin_3_7_9_6_1"
_SQ_PLUGIN_APK_RELATIVE = Path("assets") / "sq_plugin_3.7.9.6.1_2025_11_28_10_43_37.apk"
_JADX_SQ_PLUGIN_DECOMPILE_COMMAND = (
    "jadx -d <export_root>\\apk_static_index\\jadx_sq_plugin_3_7_9_6_1 "
    "--no-res --show-bad-code --threads-count 8 "
    "<apk_unpacked_root>\\assets\\sq_plugin_3.7.9.6.1_2025_11_28_10_43_37.apk"
)

_JADX_MAIN_PLUGIN_LOADER_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("main_default_plugin_name", 'private String defaultPluginApkName = "default_plugin.apk"', "主 APK 插件管理器的默认插件名。"),
    ("main_multisdk_default_plugin", "PluginMultiSdkManager.getInstance().getDefaultPlugin()", "默认插件名可由 multi-sdk 配置覆盖。"),
    ("main_asset_plugin_copy", 'copyAssetPlugin(this.defaultPluginApkName, "plugin")', "主 APK 从 assets 复制插件 APK 到运行目录。"),
    ("main_apk_class_loader", "new ApkClassLoader(", "主 APK 使用自定义 ApkClassLoader 加载插件 APK。"),
    ("main_plugin_class_target", 'private final String SDK_CLASS = "com.sqwan.msdk.SQwanCoreImpl"', "主 APK 反射目标是插件内 SQwanCoreImpl。"),
    ("main_reflect_get_instance", 'private final String SDK_GET_METHOD = "getInstance"', "主 APK 通过 getInstance 获取插件 SDK 单例。"),
    ("main_reflect_login_method", 'getMethod("login", Context.class, getClass("com.sqwan.msdk.api.SQResultListener"))', "主 APK 反射调用插件登录入口。"),
)

_JADX_SQ_PLUGIN_CORE_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("plugin_core_impl_class", "public class SQwanCoreImpl extends BaseSQwanCore", "插件 APK 内真正的 SQwanCoreImpl。"),
    ("plugin_core_get_instance", "public static SQwanCoreImpl getInstance()", "插件 SDK 单例入口。"),
    ("plugin_core_platform_factory", "return new _SQwan(context, initBean, sQResultListener);", "SQwanCoreImpl 创建 _SQwan 平台实现。"),
    ("plugin_base_login_entry", "public void login(final Context context", "BaseSQwanCore 登录入口。"),
    ("plugin_base_do_login", "private void doLogin(", "BaseSQwanCore 登录实际执行函数。"),
    ("plugin_base_sdkapi_login", "sdkapi.login(context, new SQResultListener()", "BaseSQwanCore 委托平台 sdkapi.login。"),
    ("plugin_platform_login_delegate", "loginSQ(sQResultListener);", "_SQwan/Platform 委托 37 登录。"),
    ("plugin_sq_init_key", "ZipString.zipString2Json(MultiSDKUtils.getKey(context))", "SQ 初始化使用本地 key 解出配置。"),
    ("plugin_sq37_login_call", "sq.login(context, new com.sy37sdk.core.SQResultListener()", "Platform 调用 37 SDK 登录。"),
    ("plugin_sq_login_success", "Platform.this.sqLoginSuccess", "37 SDK 成功后进入 sqLoginSuccess。"),
    ("plugin_json_login_success_callback", "protected void loginSuccessCallBack(String str", "Platform 也保留 JSON 登录成功回调解析路径。"),
    ("plugin_json_token_read", 'jSONObject2.getString("token")', "JSON 回调路径读取 token 字段名。"),
    ("plugin_bundle_token_read", 'bundle.getString("token")', "37 SDK Bundle 路径读取 token 字段名。"),
    ("plugin_bundle_userid_read", "bundle.getString(BaseSQwanCore.LOGIN_KEY_USERID)", "37 SDK Bundle 路径读取 userid。"),
    ("plugin_bundle_token_write", 'bundle2.putString("token", string3)', "sqLoginSuccess 重新组装 token。"),
    ("plugin_json_token_write", 'bundle.putString("token", string)', "JSON 回调路径重新组装 token。"),
    ("plugin_bundle_gid_write", 'bundle2.putString("gid", appConfig.getGameid())', "sqLoginSuccess 写出 gid。"),
    ("plugin_bundle_pid_write", 'bundle2.putString("pid", appConfig.getPartner())', "sqLoginSuccess 写出 pid。"),
    ("plugin_json_gid_write", 'bundle.putString("gid", appConfig.getGameid())', "JSON 回调路径写出 gid。"),
    ("plugin_json_pid_write", 'bundle.putString("pid", appConfig.getPartner())', "JSON 回调路径写出 pid。"),
    ("plugin_result_success_call", "sQResultListener.onSuccess(bundle2);", "sqLoginSuccess 回调给主 APK 的 SQResultListener。"),
    ("plugin_host_prefix_m", 'public static String APP_HOST_Prefix_m = "http://m-api."', "插件内 SDK 服务域名前缀线索。"),
    ("plugin_login_report_url", "URL_LOGIN_REPORT", "插件内登录失败上报 URL 常量。"),
)


def _fanxiu_jadx_sq_plugin_sources_root(export_base: Path) -> Path:
    return (export_base / "apk_static_index" / _JADX_SQ_PLUGIN_DIR_NAME / "sources").resolve()


def _collect_jadx_sq_plugin_core_hits(
    root: Path,
    export_base: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    main_sources_root = _fanxiu_jadx_sources_root(export_base)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    plugin_apk = (root / _SQ_PLUGIN_APK_RELATIVE).resolve()

    if not main_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少主 APK JADX 源码目录：{main_sources_root}。请先运行主 APK no-res 反编译。"
        )
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先用 portable JADX 反编译 assets 内插件 APK。"
        )

    rows: list[dict[str, object]] = []
    hit_counts: Counter[str] = Counter()
    error_marker_files: set[str] = set()
    error_marker_lines = 0
    source_counts: dict[str, int] = {}

    if plugin_apk.is_file():
        hit_counts["embedded_plugin_apk"] += 1
        rows.append(
            {
                "source_set": "apk",
                "category": "embedded_plugin_apk",
                "file": _SQ_PLUGIN_APK_RELATIVE.as_posix(),
                "line": "",
                "needle": _SQ_PLUGIN_APK_RELATIVE.name,
                "text": f"embedded plugin apk exists: {_SQ_PLUGIN_APK_RELATIVE.as_posix()}",
                "interpretation": "主 APK assets 中存在可单独反编译的 SQwan 插件 APK。",
            }
        )

    def scan_source_set(
        *,
        source_set: str,
        sources_root: Path,
        needles: tuple[tuple[str, str, str], ...],
    ) -> None:
        nonlocal error_marker_lines
        java_files = sorted(sources_root.rglob("*.java"))
        source_counts[f"{source_set}_java_source_count"] = len(java_files)
        for java_path in java_files:
            rel_path = java_path.relative_to(sources_root).as_posix()
            try:
                lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                clean_line = line.strip()
                if "JADX ERROR" in clean_line:
                    error_marker_files.add(f"{source_set}:{rel_path}")
                    error_marker_lines += 1
                for category, needle, interpretation in needles:
                    if needle not in clean_line:
                        continue
                    hit_counts[category] += 1
                    rows.append(
                        {
                            "source_set": source_set,
                            "category": category,
                            "file": rel_path,
                            "line": line_no,
                            "needle": needle,
                            "text": clean_line,
                            "interpretation": interpretation,
                        }
                    )

    scan_source_set(source_set="main_apk", sources_root=main_sources_root, needles=_JADX_MAIN_PLUGIN_LOADER_NEEDLES)
    scan_source_set(source_set="sq_plugin", sources_root=plugin_sources_root, needles=_JADX_SQ_PLUGIN_CORE_NEEDLES)

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    verdict: dict[str, object] = {
        "embedded_plugin_apk_found": plugin_apk.is_file(),
        "main_jadx_sources_present": source_counts.get("main_apk_java_source_count", 0) > 0,
        "plugin_jadx_sources_present": source_counts.get("sq_plugin_java_source_count", 0) > 0,
        "plugin_loader_boundary_found": has("main_asset_plugin_copy")
        and has("main_apk_class_loader")
        and has("main_plugin_class_target"),
        "reflection_login_to_plugin_found": has("main_reflect_login_method") and has("main_reflect_get_instance"),
        "sqwan_core_impl_found": has("plugin_core_impl_class") and has("plugin_core_platform_factory"),
        "base_login_delegates_to_platform": has("plugin_base_login_entry")
        and has("plugin_base_do_login")
        and has("plugin_base_sdkapi_login"),
        "sq37_login_delegation_found": has("plugin_platform_login_delegate")
        and has("plugin_sq37_login_call")
        and has("plugin_sq_login_success"),
        "platform_login_success_builds_bundle": has("plugin_bundle_token_write")
        and has("plugin_bundle_gid_write")
        and has("plugin_bundle_pid_write")
        and has("plugin_result_success_call"),
        "json_login_success_path_found": has("plugin_json_login_success_callback")
        and has("plugin_json_token_read")
        and has("plugin_json_token_write"),
        "bundle_token_gid_pid_found": has("plugin_bundle_token_read")
        and has("plugin_bundle_token_write")
        and has("plugin_bundle_gid_write")
        and has("plugin_bundle_pid_write"),
        "sdk_host_prefix_found": has("plugin_host_prefix_m"),
        "decompile_has_error_markers": bool(error_marker_files),
        "safe_static_template_only": True,
    }
    stats: dict[str, object] = {
        **source_counts,
        "plugin_apk": str(plugin_apk),
        "hit_rows": len(rows),
        "hit_categories": dict(hit_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    rows.sort(key=lambda row: (str(row["source_set"]), str(row["file"]), str(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_sq_plugin_core_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    confidence = 95 if verdict.get("plugin_loader_boundary_found") and verdict.get("sqwan_core_impl_found") else 65
    return [
        {
            "step": "01-main-plugin-loader",
            "source": "PluginLoader / PluginManager",
            "target": "assets/sq_plugin_3.7.9.6.1_2025_11_28_10_43_37.apk -> ApkClassLoader",
            "confidence": 95 if verdict.get("plugin_loader_boundary_found") else 55,
            "note": "主 APK 是 SDK 壳，真正实现从 assets 插件 APK 装载。",
        },
        {
            "step": "02-reflection-facade",
            "source": "PluginSQwanCore.login",
            "target": "com.sqwan.msdk.SQwanCoreImpl.getInstance().login(...)",
            "confidence": 95 if verdict.get("reflection_login_to_plugin_found") else 55,
            "note": "主 APK 通过反射跨 classloader 调用插件实现。",
        },
        {
            "step": "03-plugin-platform",
            "source": "SQwanCoreImpl.getPlatform",
            "target": "new _SQwan(context, initBean, listener)",
            "confidence": 95 if verdict.get("sqwan_core_impl_found") else 55,
            "note": "插件 APK 内的 SQwanCoreImpl 创建具体平台实现。",
        },
        {
            "step": "04-login-delegate",
            "source": "BaseSQwanCore.login / doLogin",
            "target": "sdkapi.login(context, SQResultListener)",
            "confidence": 95 if verdict.get("base_login_delegates_to_platform") else 55,
            "note": "BaseSQwanCore 处理初始化和弹窗后，把登录委托给平台层。",
        },
        {
            "step": "05-sq37-sdk",
            "source": "_SQwan.loginPlatform / Platform.loginSQ",
            "target": "com.sy37sdk.core.SQwan.login",
            "confidence": 95 if verdict.get("sq37_login_delegation_found") else 55,
            "note": "登录继续进入 37 SDK 层，成功后回到 sqLoginSuccess。",
        },
        {
            "step": "06-bundle-back-to-main-apk",
            "source": "Platform.sqLoginSuccess",
            "target": "Bundle(token, gid, pid, userid, username) -> SQResultListener.onSuccess",
            "confidence": 95 if verdict.get("bundle_token_gid_pid_found") else 55,
            "note": "这里只记录字段名和变量流，不采集真实账号 token。",
        },
        {
            "step": "07-unity-callback",
            "source": "main APK SQResultListener.onSuccess",
            "target": "SDKCallback.OnSDKLoginData(\"1__\" + token + \"__\" + gid + \"__\" + pid)",
            "confidence": confidence,
            "note": "该步骤与上一份 JADX login source report 衔接，形成 Java 壳到 Unity 的完整静态链路。",
        },
    ]


def _write_jadx_sq_plugin_core_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    main_sources_root: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX SQ plugin core report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- Main APK JADX sources: `{main_sources_root}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        f"- Embedded plugin APK: `{stats.get('plugin_apk', '')}`",
        f"- Portable Java: `{_JADX_PORTABLE_JAVA_EXE}`",
        f"- Portable JADX: `{_JADX_PORTABLE_EXE}`",
        f"- Plugin decompile command shape: `{_JADX_SQ_PLUGIN_DECOMPILE_COMMAND}`",
        "- Scope: static Java source evidence only. This report records class boundaries, field names, and call routes; it does not collect live account token values.",
        "",
        "## Conclusion",
        "",
        "- The main APK contains an embedded SQwan plugin APK under `assets/` and loads it through `ApkClassLoader`.",
        "- The main APK `PluginSQwanCore` facade reflects into `com.sqwan.msdk.SQwanCoreImpl` from the plugin APK.",
        "- The plugin APK contains the real login chain: `SQwanCoreImpl -> BaseSQwanCore.login/doLogin -> _SQwan/Platform.loginSQ -> com.sy37sdk.core.SQwan.login -> Platform.sqLoginSuccess`.",
        "- `Platform.sqLoginSuccess` reconstructs a Bundle containing `token`, `gid`, and `pid`, then calls the listener that the main APK later converts into the Unity payload family.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| main_apk_java_source_count | {stats.get('main_apk_java_source_count', 0)} |",
            f"| sq_plugin_java_source_count | {stats.get('sq_plugin_java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=360)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(
        [
            "",
            "## Hit Categories",
            "",
            "| category | count |",
            "| --- | ---: |",
        ]
    )
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| source_set | category | source | line | text | interpretation |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:180]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('source_set', ''))}` | "
            f"`{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''))}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sq_plugin_core_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    main_sources_root = _fanxiu_jadx_sources_root(export_base)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, meta = _collect_jadx_sq_plugin_core_hits(root, export_base)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_sq_plugin_core_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_sq_plugin_core_hits.tsv",
        ["source_set", "category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_sq_plugin_core_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_sq_plugin_core_markdown(
        output_dir / "apk_jadx_sq_plugin_core_report.md",
        root=root,
        export_base=export_base,
        main_sources_root=main_sources_root,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "main_sources_root": str(main_sources_root),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tools": {
            "java": str(_JADX_PORTABLE_JAVA_EXE),
            "jadx": str(_JADX_PORTABLE_EXE),
            "plugin_decompile_command": _JADX_SQ_PLUGIN_DECOMPILE_COMMAND,
        },
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sq_plugin_core_report.md"),
            "hits": str(output_dir / "apk_jadx_sq_plugin_core_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_sq_plugin_core_flow.tsv"),
            "json": str(output_dir / "apk_jadx_sq_plugin_core_report.json"),
        },
    }
    (output_dir / "apk_jadx_sq_plugin_core_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_SY37_LOGIN_ACCOUNT_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("sy37_sqwan_login_entry", "public void login(Context context, final SQResultListener sQResultListener)", "37 SDK SQwan facade 登录入口。"),
    ("sy37_sqwan_manager_login", "public void login(final SQResultListener sQResultListener)", "SQwanManager 登录入口。"),
    ("sy37_account_mod_login_call", "((IAccountMod) ModHelper.get(IAccountMod.class)).login", "SQwanManager 转入账号模块 IAccountMod.login。"),
    ("sy37_account_mod_login_entry", "public void login(ILoginListener iLoginListener)", "AccountModImpl 接收 SDK 登录请求。"),
    ("sy37_ui_controller_select", "UIVersionManager.getInstance(this.mContext).getLoginController()", "AccountModImpl 选择登录控制器。"),
    ("sy37_fast_verify_default_controller", "this.mLoginController = new FastVerifyController(this.mContext);", "UIVersionManager 默认使用 FastVerifyController。"),
    ("sy37_fast_verify_controller_to_manager", "AccountLoginManager.getInstance(this.mContext).login(iLoginListener)", "FastVerifyController 进入 AccountLoginManager。"),
    ("sy37_cached_auto_login", "showAutoLoginDialog(mobile, iLoginListener);", "AbstractLoginController 发现缓存账号时走自动登录弹窗。"),
    ("sy37_show_login_dialog", "showLoginDialog(iLoginListener);", "无有效缓存时展示原生登录弹窗。"),
    ("sy37_fast_verify_login", "doFastVerifyLogin(new FastLoginManager.FastLoginListener()", "AccountLoginManager 尝试闪验/一键登录。"),
    ("sy37_account_login_logic", "public void accountLogin(final String str, final String str2", "AccountLogic 账号密码登录入口。"),
    ("sy37_account_login_request", "this.requestManager.loginRequest(str, encryptPwd(str2)", "账号密码登录请求前对密码做本地加密。"),
    ("sy37_account_request_login_url", "SqRequest.of(UrlConstant.LOGIN).signV3()", "账号密码登录使用 UrlConstant.LOGIN + signV3。"),
    ("sy37_account_request_login_params", '.addParam("uname", str).addParam(SqConstants.UPWD, str2)', "账号密码登录请求字段含 uname/upwd。"),
    ("sy37_dtoken_header", 'addHeader("D-Token", DevicesFingerprint.getDevToken', "登录请求带设备指纹 D-Token header。"),
    ("sy37_common_params_v2", "addParamsTransformer(new CommonParamsV2())", "账号密码路径附加 CommonParamsV2。"),
    ("sy37_common_params_v3", "addParamsTransformer(new CommonParamsV3())", "手机/闪验/微信路径附加 CommonParamsV3。"),
    ("sy37_encrypt_pwd", "AESUtil.encrypt(str, getEncodeKey())", "密码本地 AES 加密后再传给账号请求。"),
    ("sy37_encode_key_appkey", "ConfigManager.getInstance(this.context).getAppKey()", "密码加密 key 来源于 appKey 的 16 字节形状。"),
    ("sy37_login_success_handler", "private void handleLoginSuccess(String str, String str2", "AccountLogic 登录成功统一处理函数。"),
    ("sy37_userinfo_token_read", 'jSONObject.optString("token")', "登录响应 JSON 读取 token 字段名。"),
    ("sy37_success_map_userid", "map.put(BaseSQwanCore.LOGIN_KEY_USERID", "成功 map 写 userid。"),
    ("sy37_success_map_username", "map.put(BaseSQwanCore.LOGIN_KEY_USERNAME", "成功 map 写 username。"),
    ("sy37_success_map_token", 'map.put("token", userInfoCreateUserInfo.getToken())', "成功 map 写 token。"),
    ("sy37_success_map_pid", 'map.put("pid", ConfigManager.getInstance(this.context).getSQAppConfig().getPartner())', "成功 map 写 pid。"),
    ("sy37_success_map_gid", 'map.put("gid", ConfigManager.getInstance(this.context).getSQAppConfig().getGameid())', "成功 map 写 gid。"),
    ("sy37_success_listener", "this.loginListener.onSuccess(map);", "账号模块成功回调回 SQwanManager。"),
    ("sy37_phone_pwd_request", "SqRequest.of(UrlConstant.URL_LOGIN_PHONE_PWD).signV3()", "手机号密码登录端点。"),
    ("sy37_phone_code_request", "SqRequest.of(UrlConstant.URL_LOGIN_CHECK_CODE).signV3()", "手机号验证码登录端点。"),
    ("sy37_phone_ticket_request", "SqRequest.of(UrlConstant.URL_LOGIN_TICKET).signV3()", "ticket 登录端点。"),
    ("sy37_wechat_request", "SqRequest.of(UrlConstant.WECHAT_LOGIN).signV3()", "微信登录端点。"),
    ("sy37_fast_token_verify_request", "SqRequest.of(UrlConstant.URL_VERIFY_FAST_TOKEN).signV3()", "闪验 token 校验端点。"),
    ("sy37_fast_login_request", "SqRequest.of(UrlConstant.URL_LOGIN_FAST).signV3()", "快速登录端点。"),
    ("sy37_url_login_default", 'public static String LOGIN = "http://s-api-secure."', "账号密码默认登录 URL 常量。"),
    ("sy37_url_phone_pwd", 'URL_LOGIN_PHONE_PWD = "http://s-api-secure."', "手机号密码登录 URL 常量。"),
    ("sy37_url_phone_code", 'URL_LOGIN_CHECK_CODE = "http://s-api-secure."', "手机号验证码登录 URL 常量。"),
    ("sy37_url_fast_token", 'URL_VERIFY_FAST_TOKEN = "http://s-api-secure."', "闪验 token 校验 URL 常量。"),
    ("sy37_url_fast_login", 'URL_LOGIN_FAST = "http://s-api-secure."', "快速登录 URL 常量。"),
    ("sy37_url_wechat", 'WECHAT_LOGIN = "http://s-api-secure."', "微信登录 URL 常量。"),
)


def _collect_jadx_sy37_login_account_hits(export_base: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    java_files = sorted(plugin_sources_root.rglob("*.java"))
    rows: list[dict[str, object]] = []
    hit_counts: Counter[str] = Counter()
    error_marker_files: set[str] = set()
    error_marker_lines = 0

    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if "JADX ERROR" in clean_line:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            for category, needle, interpretation in _JADX_SY37_LOGIN_ACCOUNT_NEEDLES:
                if needle not in clean_line:
                    continue
                hit_counts[category] += 1
                rows.append(
                    {
                        "category": category,
                        "file": rel_path,
                        "line": line_no,
                        "needle": needle,
                        "text": clean_line,
                        "interpretation": interpretation,
                    }
                )

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    verdict: dict[str, object] = {
        "plugin_jadx_sources_present": bool(java_files),
        "sqwan_to_account_mod_found": has("sy37_sqwan_login_entry")
        and has("sy37_sqwan_manager_login")
        and has("sy37_account_mod_login_call")
        and has("sy37_account_mod_login_entry"),
        "native_login_controller_found": has("sy37_ui_controller_select")
        and has("sy37_fast_verify_default_controller")
        and has("sy37_fast_verify_controller_to_manager"),
        "auto_login_or_dialog_split_found": has("sy37_cached_auto_login") and has("sy37_show_login_dialog"),
        "account_password_http_path_found": has("sy37_account_login_logic")
        and has("sy37_account_login_request")
        and has("sy37_account_request_login_url")
        and has("sy37_account_request_login_params"),
        "password_local_aes_before_request_found": has("sy37_encrypt_pwd") and has("sy37_encode_key_appkey"),
        "phone_login_http_paths_found": has("sy37_phone_pwd_request")
        and has("sy37_phone_code_request")
        and has("sy37_phone_ticket_request"),
        "fast_verify_http_path_found": has("sy37_fast_verify_login")
        and has("sy37_fast_token_verify_request")
        and has("sy37_fast_login_request"),
        "wechat_http_path_found": has("sy37_wechat_request"),
        "success_map_token_gid_pid_found": has("sy37_userinfo_token_read")
        and has("sy37_success_map_token")
        and has("sy37_success_map_pid")
        and has("sy37_success_map_gid")
        and has("sy37_success_listener"),
        "login_url_constants_found": has("sy37_url_login_default")
        and has("sy37_url_phone_pwd")
        and has("sy37_url_fast_token")
        and has("sy37_url_wechat"),
        "device_token_header_found": has("sy37_dtoken_header"),
        "decompile_has_error_markers": bool(error_marker_files),
        "safe_static_template_only": True,
    }
    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "hit_rows": len(rows),
        "hit_categories": dict(hit_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    rows.sort(key=lambda row: (str(row["file"]), str(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_sy37_login_account_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "step": "01-sqwan-to-account-mod",
            "source": "SQwan.login / SQwanManager.login",
            "target": "IAccountMod.login(ILoginListener)",
            "confidence": 95 if verdict.get("sqwan_to_account_mod_found") else 55,
            "note": "37 SDK facade 将登录委托给账号模块。",
        },
        {
            "step": "02-native-login-controller",
            "source": "AccountModImpl.login",
            "target": "UIVersionManager.getLoginController -> FastVerifyController",
            "confidence": 95 if verdict.get("native_login_controller_found") else 55,
            "note": "默认控制器是原生登录/闪验控制器，不是简单 WebView 页面。",
        },
        {
            "step": "03-login-ui-split",
            "source": "AbstractLoginController.login",
            "target": "cached account auto-login dialog or normal login dialog",
            "confidence": 90 if verdict.get("auto_login_or_dialog_split_found") else 55,
            "note": "缓存账号走自动登录弹窗，否则展示原生登录弹窗。",
        },
        {
            "step": "04-account-password-http",
            "source": "AccountLogic.accountLogin",
            "target": "AccountRequestManager.loginRequest -> UrlConstant.LOGIN",
            "confidence": 95 if verdict.get("account_password_http_path_found") else 55,
            "note": "账号密码路径是签名 HTTP POST，参数含 uname/upwd/D-Token/CommonParams。",
        },
        {
            "step": "05-phone-fast-wechat-http",
            "source": "LoginRequestManager / FastLoginHttpUtil",
            "target": "phone pwd/code/ticket, quick login, wechat login endpoints",
            "confidence": 90
            if verdict.get("phone_login_http_paths_found") and verdict.get("fast_verify_http_path_found")
            else 55,
            "note": "手机号、闪验、微信都是 SDK 账号 HTTP 路径；WebView 主要是辅助/网页能力。",
        },
        {
            "step": "06-success-map",
            "source": "AccountLogic.handleLoginSuccess",
            "target": "Map(userid, username, token, pid, gid) -> ILoginListener.onSuccess",
            "confidence": 95 if verdict.get("success_map_token_gid_pid_found") else 55,
            "note": "这里只记录字段名和变量流，不读取或保存本机账号缓存值。",
        },
        {
            "step": "07-back-to-unity-chain",
            "source": "SQwanManager.convertMapToBundle",
            "target": "Platform.sqLoginSuccess -> main APK SDKCallback -> UnitySendMessage",
            "confidence": 95
            if verdict.get("success_map_token_gid_pid_found") and verdict.get("sqwan_to_account_mod_found")
            else 55,
            "note": "该报告与 SQ plugin core report 衔接，补足 37 账号模块到 token/gid/pid Bundle 的来源。",
        },
    ]


def _write_jadx_sy37_login_account_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX sy37 login account report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        f"- Portable Java: `{_JADX_PORTABLE_JAVA_EXE}`",
        f"- Portable JADX: `{_JADX_PORTABLE_EXE}`",
        "- Scope: static Java source evidence only. This report records account-module routes, URL constants, request field names, and callback field names; it does not read local account cache or collect live token/password values.",
        "",
        "## Conclusion",
        "",
        "- The 37 SDK login path is `SQwan.login -> SQwanManager.login -> IAccountMod.login -> AccountModImpl.login -> UIVersionManager/FastVerifyController -> AccountLoginManager/AccountLogic`.",
        "- The normal account-password route is a signed HTTP POST to `UrlConstant.LOGIN`, carrying field names such as `uname`, `upwd`, `D-Token`, `trans_info`, and common SDK parameters.",
        "- Password text is locally AES-encrypted with an appKey-derived 16-byte key before the account-password request; this report records the algorithm shape only, not any real password or key value.",
        "- Phone password/code/ticket, one-key/flash login, and WeChat login are separate SDK HTTP routes; WebView exists in the SDK but is not the main account-password login path shown here.",
        "- `handleLoginSuccess` parses response JSON into `uid/uname/token`, adds `pid/gid`, then calls the listener map that later becomes the SQwan Bundle.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| sq_plugin_java_source_count | {stats.get('sq_plugin_java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=360)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(
        [
            "",
            "## Hit Categories",
            "",
            "| category | count |",
            "| --- | ---: |",
        ]
    )
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| category | source | line | text | interpretation |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:180]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''))}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_login_account_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, meta = _collect_jadx_sy37_login_account_hits(export_base)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_sy37_login_account_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_sy37_login_account_hits.tsv",
        ["category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_sy37_login_account_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_sy37_login_account_markdown(
        output_dir / "apk_jadx_sy37_login_account_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_login_account_report.md"),
            "hits": str(output_dir / "apk_jadx_sy37_login_account_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_sy37_login_account_flow.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_login_account_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_login_account_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_SY37_LOGIN_RESPONSE_NEEDLES: tuple[tuple[str, str, str, str], ...] = (
    ("plugin", "response_json_uid", 'jSONObject.optString("uid")', "AccountLogic.createUserInfo reads uid from login response JSON."),
    ("plugin", "response_json_uname", 'jSONObject.optString("uname")', "AccountLogic.createUserInfo reads uname from login response JSON."),
    ("plugin", "response_json_token", 'jSONObject.optString("token")', "AccountLogic.createUserInfo/UserInfo reads token from login response JSON."),
    ("plugin", "response_json_refresh_token", "jSONObject.optString(SqConstants.REFRESH_TOKEN)", "AccountLogic.createUserInfo reads refresh token field."),
    ("plugin", "response_json_login_account", 'jSONObject.optString("login_account")', "AccountLogic.createUserInfo reads login_account as account alias."),
    ("plugin", "response_json_action_type", 'jSONObject.optString("action_type")', "AccountLogic.createUserInfo reads action_type."),
    ("plugin", "response_json_pwd", 'jSONObject.optString("pwd")', "AccountLogic can decrypt pwd returned by response/cache JSON."),
    ("plugin", "response_userinfo_set_uid", "userInfo.setUid(strOptString)", "Response uid is stored into sy37 UserInfo."),
    ("plugin", "response_userinfo_set_uname", "userInfo.setUname(strOptString2)", "Response uname is stored into sy37 UserInfo."),
    ("plugin", "response_userinfo_set_token", "userInfo.setToken(strOptString3)", "Response token is stored into sy37 UserInfo."),
    ("plugin", "response_userinfo_set_refresh_token", "userInfo.setRefreshToken(strOptString4)", "Refresh token is stored into sy37 UserInfo."),
    ("plugin", "response_userinfo_set_alias", "userInfo.setAlias(strOptString5)", "login_account is stored as alias."),
    ("plugin", "response_cache_set_userinfo", "AccountCache.setUserInfo(this.context, userInfoCreateUserInfo);", "Login success writes UserInfo to AccountCache."),
    ("plugin", "response_account_file_write", "AccountTools.setAccountToFile(this.context, userInfoCreateUserInfo);", "Login success persists UserInfo via AccountTools."),
    ("plugin", "response_common_userinfo_set", "UserInfoManager.getInstance().setLoginUser(userInfoConvert);", "Converted common UserInfo is stored in UserInfoManager."),
    ("plugin", "success_map_userid", "map.put(BaseSQwanCore.LOGIN_KEY_USERID, userInfoCreateUserInfo.getUid());", "Success map carries userid."),
    ("plugin", "success_map_username", "map.put(BaseSQwanCore.LOGIN_KEY_USERNAME, userInfoCreateUserInfo.getUname());", "Success map carries username."),
    ("plugin", "success_map_token", 'map.put("token", userInfoCreateUserInfo.getToken());', "Success map carries token."),
    ("plugin", "success_map_pwd", 'map.put("pwd", userInfoCreateUserInfo.getUpwd());', "Success map carries cached/decoded password field."),
    ("plugin", "success_map_pid", 'map.put("pid", ConfigManager.getInstance(this.context).getSQAppConfig().getPartner());', "Success map adds partner id."),
    ("plugin", "success_map_gid", 'map.put("gid", ConfigManager.getInstance(this.context).getSQAppConfig().getGameid());', "Success map adds game id."),
    ("plugin", "success_listener_map", "this.loginListener.onSuccess(map);", "AccountLogic emits the success map to ILoginListener."),
    ("plugin", "sqwan_convert_map_to_bundle", "public Bundle convertMapToBundle(Map<String, String> map)", "SQwanManager converts success map entries to Bundle."),
    ("plugin", "bundle_put_entry", "bundle.putString(entry.getKey(), entry.getValue());", "SQwanManager copies every map entry into Bundle."),
    ("plugin", "platform_get_bundle_userid", "String string = bundle.getString(BaseSQwanCore.LOGIN_KEY_USERID);", "Platform.sqLoginSuccess reads userid from Bundle."),
    ("plugin", "platform_get_bundle_username", "String string2 = bundle.getString(BaseSQwanCore.LOGIN_KEY_USERNAME);", "Platform.sqLoginSuccess reads username from Bundle."),
    ("plugin", "platform_get_bundle_token", 'String string3 = bundle.getString("token");', "Platform.sqLoginSuccess reads token from Bundle."),
    ("plugin", "platform_store_userid", "MultiSDKUtils.setUserid(context2, string);", "Platform stores userid in MultiSDKUtils."),
    ("plugin", "platform_store_username", "MultiSDKUtils.setUsername(context2, string2);", "Platform stores username in MultiSDKUtils."),
    ("plugin", "platform_store_token", "MultiSDKUtils.setToken(context2, string3);", "Platform stores token in MultiSDKUtils."),
    ("plugin", "platform_out_token", 'bundle2.putString("token", string3);', "Platform output Bundle carries token to main APK."),
    ("plugin", "platform_out_gid", 'bundle2.putString("gid", appConfig.getGameid());', "Platform output Bundle carries gid to main APK."),
    ("plugin", "platform_out_pid", 'bundle2.putString("pid", appConfig.getPartner());', "Platform output Bundle carries pid to main APK."),
    ("plugin", "platform_success_bundle", "sQResultListener.onSuccess(bundle2);", "Platform emits the final login Bundle."),
    ("main", "main_sdk_login_entry", "SQwanCore.getInstance().login(this, new SQResultListener()", "Main APK starts SDK login through SQwanCore."),
    ("main", "main_bundle_token", 'String string = bundle.getString("token");', "Main APK login callback reads token from Bundle."),
    ("main", "main_bundle_gid", 'FlameUnityActivity.m_gid = bundle.getString("gid");', "Main APK login callback reads gid from Bundle."),
    ("main", "main_bundle_pid", "FlameUnityActivity.m_pid = bundle.getString(PushConsts.KEY_SERVICE_PIT);", "Main APK login callback reads pid from Bundle."),
    ("main", "main_login_payload", 'SDKCallback.OnSDKLoginData("1__" + string + "__" + FlameUnityActivity.m_gid + "__" + FlameUnityActivity.m_pid);', "Main APK serializes success as 1__token__gid__pid."),
    ("main", "main_login_failure_payload", 'SDKCallback.OnSDKLoginData("0__" + str);', "Main APK serializes selected login failure as 0__message."),
    ("main", "sdkcallback_login_data", "public static void OnSDKLoginData(String str)", "SDKCallback receives serialized login data."),
    ("main", "sdkcallback_on_receive_login", 'SendDataToUnity("OnReceiveLogin", str);', "SDKCallback sends login data to Unity method OnReceiveLogin."),
    ("main", "unity_send_message", 'UnityPlayer.UnitySendMessage("GameEnter", str, str2);', "Main APK sends data to GameEnter Unity receiver."),
)


def _collect_jadx_sy37_login_response_surface_hits(
    export_base: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    main_sources_root = _fanxiu_jadx_sources_root(export_base)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )
    if not main_sources_root.is_dir():
        raise FanxiuResourceError(f"缺少主 APK JADX 源码目录：{main_sources_root}。请先生成 JADX 源码。")

    source_roots = {"plugin": plugin_sources_root, "main": main_sources_root}
    rows: list[dict[str, object]] = []
    hit_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    error_marker_files: set[str] = set()
    error_marker_lines = 0

    for source_set, root in source_roots.items():
        java_files = sorted(root.rglob("*.java"))
        source_counts[source_set] = len(java_files)
        needles = [item for item in _JADX_SY37_LOGIN_RESPONSE_NEEDLES if item[0] == source_set]
        for java_path in java_files:
            rel_path = java_path.relative_to(root).as_posix()
            try:
                lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                clean_line = line.strip()
                if "JADX ERROR" in clean_line:
                    error_marker_files.add(f"{source_set}:{rel_path}")
                    error_marker_lines += 1
                for _, category, needle, interpretation in needles:
                    if needle not in clean_line:
                        continue
                    hit_counts[category] += 1
                    rows.append(
                        {
                            "source_set": source_set,
                            "category": category,
                            "file": rel_path,
                            "line": line_no,
                            "needle": needle,
                            "text": clean_line,
                            "interpretation": interpretation,
                        }
                    )

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    verdict: dict[str, object] = {
        "main_jadx_sources_present": source_counts.get("main", 0) > 0,
        "plugin_jadx_sources_present": source_counts.get("plugin", 0) > 0,
        "login_response_json_fields_found": all(
            has(category)
            for category in (
                "response_json_uid",
                "response_json_uname",
                "response_json_token",
                "response_json_refresh_token",
                "response_json_login_account",
                "response_json_action_type",
            )
        ),
        "userinfo_object_population_found": all(
            has(category)
            for category in (
                "response_userinfo_set_uid",
                "response_userinfo_set_uname",
                "response_userinfo_set_token",
                "response_userinfo_set_refresh_token",
            )
        ),
        "login_cache_writes_found": all(
            has(category)
            for category in ("response_cache_set_userinfo", "response_account_file_write", "response_common_userinfo_set")
        ),
        "success_map_fields_found": all(
            has(category)
            for category in ("success_map_userid", "success_map_username", "success_map_token", "success_map_pid", "success_map_gid")
        ),
        "sqwan_bundle_conversion_found": has("sqwan_convert_map_to_bundle") and has("bundle_put_entry"),
        "platform_final_bundle_found": has("platform_out_token") and has("platform_out_gid") and has("platform_out_pid"),
        "main_unity_login_payload_found": has("main_login_payload") and has("sdkcallback_on_receive_login") and has("unity_send_message"),
        "safe_static_field_names_only": True,
    }
    stats: dict[str, object] = {
        "main_java_source_count": source_counts.get("main", 0),
        "sq_plugin_java_source_count": source_counts.get("plugin", 0),
        "hit_rows": len(rows),
        "hit_categories": dict(hit_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    rows.sort(key=lambda row: (str(row["source_set"]), str(row["file"]), int(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_sy37_login_response_surface_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "step": "01-http-response-json",
            "source": "AccountLogic.createUserInfo(responseString)",
            "target": "uid / uname / token / refresh_token / login_account / action_type",
            "confidence": 95 if verdict.get("login_response_json_fields_found") else 55,
            "note": "Only response field names are recorded; no live response values are collected.",
        },
        {
            "step": "02-userinfo-object",
            "source": "createUserInfo",
            "target": "sy37 UserInfo setters",
            "confidence": 95 if verdict.get("userinfo_object_population_found") else 55,
            "note": "The response fields populate the local UserInfo object used by the account module.",
        },
        {
            "step": "03-local-state",
            "source": "handleLoginSuccess",
            "target": "AccountCache / AccountTools / UserInfoManager",
            "confidence": 90 if verdict.get("login_cache_writes_found") else 55,
            "note": "Static evidence shows where login state is stored; this probe does not read local cache values.",
        },
        {
            "step": "04-success-map",
            "source": "handleLoginSuccess",
            "target": "Map(userid, username, token, pwd, pid, gid, account_type, login_type)",
            "confidence": 95 if verdict.get("success_map_fields_found") else 55,
            "note": "pid/gid are added from SQAppConfig; token comes from response-derived UserInfo.",
        },
        {
            "step": "05-sqwan-bundle",
            "source": "SQwanManager.convertMapToBundle",
            "target": "Bundle key/value copy",
            "confidence": 95 if verdict.get("sqwan_bundle_conversion_found") else 55,
            "note": "SQwanManager passes map entries through to a Bundle.",
        },
        {
            "step": "06-platform-final-bundle",
            "source": "Platform.sqLoginSuccess",
            "target": "Bundle(token, gid, pid, userid, username)",
            "confidence": 95 if verdict.get("platform_final_bundle_found") else 55,
            "note": "Platform stores SDK login state and emits the final Bundle to the main APK callback.",
        },
        {
            "step": "07-main-apk-unity",
            "source": "FlameUnityActivity.SDKLogin",
            "target": "SDKCallback.OnSDKLoginData('1__token__gid__pid') -> UnitySendMessage(GameEnter, OnReceiveLogin, data)",
            "confidence": 95 if verdict.get("main_unity_login_payload_found") else 55,
            "note": "This is the Java-side serialization boundary before Unity/IL2CPP parses the login data.",
        },
    ]


def _write_jadx_sy37_login_response_surface_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    main_sources_root: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX sy37 login response surface report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- Main APK JADX sources: `{main_sources_root}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        "- Scope: static field-name and callback-flow evidence only. This report does not read local account caches, capture live responses, or store real token/account values.",
        "",
        "## Conclusion",
        "",
        "- SDK HTTP login responses feed `AccountLogic.createUserInfo`, which statically reads `uid`, `uname`, `token`, `refresh_token`, `login_account`, `action_type`, and optional password fields.",
        "- `handleLoginSuccess` stores a `UserInfo` object locally and emits a success map containing `userid`, `username`, `token`, `pwd`, `pid`, `gid`, account type, and login type.",
        "- `SQwanManager.convertMapToBundle` copies the success map into a Bundle; `Platform.sqLoginSuccess` emits the main APK-facing Bundle with `token/gid/pid/userid/username`.",
        "- The main APK `FlameUnityActivity.SDKLogin` serializes successful login as `1__token__gid__pid`, then `SDKCallback` forwards it to Unity `GameEnter.OnReceiveLogin`.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| main_java_source_count | {stats.get('main_java_source_count', 0)} |",
            f"| sq_plugin_java_source_count | {stats.get('sq_plugin_java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''), limit=260)}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(["", "## Hit Categories", "", "| category | count |", "| --- | ---: |"])
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| source_set | category | source | line | text | interpretation |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:180]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('source_set', ''))}` | "
            f"`{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''), limit=160)}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=430)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_login_response_surface_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    main_sources_root = _fanxiu_jadx_sources_root(export_base)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, meta = _collect_jadx_sy37_login_response_surface_hits(export_base)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_sy37_login_response_surface_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_sy37_login_response_surface_hits.tsv",
        ["source_set", "category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_sy37_login_response_surface_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_sy37_login_response_surface_markdown(
        output_dir / "apk_jadx_sy37_login_response_surface_report.md",
        root=root,
        export_base=export_base,
        main_sources_root=main_sources_root,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "main_sources_root": str(main_sources_root),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_login_response_surface_report.md"),
            "hits": str(output_dir / "apk_jadx_sy37_login_response_surface_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_sy37_login_response_surface_flow.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_login_response_surface_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_login_response_surface_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_SY37_REQUEST_SIGNING_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("sqrequest_sign_v3_tag", "this.mRequestBuilder.tag(SignInterceptor.SignVersion.class, SignInterceptor.SignVersion.V3);", "SqRequest.signV3 给请求打 V3 签名版本标签。"),
    ("sqrequest_params_transformer_api", "public SqRequest addParamsTransformer(RequestBuilder.ParamsTransformer paramsTransformer)", "SqRequest 允许调用方追加公共参数变换器。"),
    ("sqrequest_stringify_transformer", "this.mRequestBuilder.addParamsTransformer(new StringifyTransformer());", "入队前会把参数规范化为字符串形态。"),
    ("sqrequest_ip_header_transformer", "this.mRequestBuilder.addHeadersTransformer(new IpHeaderTransformer());", "入队前追加 SDK 自己的 header transformer。"),
    ("httpclient_request_id_interceptor", "addInterceptor(new RequestIdOkInterceptor())", "OkHttp 链路先追加 Request-Id 类 header。"),
    ("httpclient_sign_interceptor", "addInterceptor(new SignInterceptor())", "OkHttp 链路包含签名拦截器。"),
    ("httpclient_gateway_interceptor", "new GateWayEncryptInterceptor(", "OkHttp 链路包含统一网关加密层。"),
    ("sign_interceptor_appkey_source", "ConfigManager.getInstance(SQContextWrapper.getApplicationContext()).getAppKey()", "SignInterceptor 从 ConfigManager 取 appKey；报告不读取本机真实 key。"),
    ("sign_interceptor_v3_instance", "this.mV3 = new SignV3Interceptor(appKey);", "V3 签名拦截器以 appKey 初始化。"),
    ("sign_interceptor_v3_dispatch", "return this.mV3.intercept(chain);", "SignInterceptor 按请求标签分派到 V3。"),
    ("sign_v3_param_name", 'private static final String PARAM_NAME = "sign";', "V3 签名参数名是 sign。"),
    ("sign_v3_form_skip_existing", 'if (!"sign".equals(strName))', "表单签名时先排除已有 sign 字段。"),
    ("sign_v3_form_add_sign", 'builder.add("sign", getSign(request, map));', "表单请求在请求体补 sign。"),
    ("sign_v3_get_add_sign", 'addQueryParameter("sign", getSign(request, map2))', "GET 请求在 query 上补 sign。"),
    ("sign_v3_sort_keys", "Collections.sort(arrayList);", "V3 签名前按参数名排序。"),
    ("sign_v3_append_value", "sb.append(map.get(str2));", "V3 签名原串拼接参数值。"),
    ("sign_v3_md5_lower", "MD5Util.Md5(sb.toString()).toLowerCase(Locale.US)", "V3 签名结果是 MD5 后小写。"),
    ("request_id_headers", 'addHeader("Request-Id", RequestUtil.generateRequestId())', "RequestIdOkInterceptor 写 Request-Id。"),
    ("request_liveid_header", 'addHeader("Request-LiveId", SpRequestInfo.getRequestLiveId', "RequestIdOkInterceptor 写 Request-LiveId。"),
    ("gateway_provider_key", "return GateWayManager.getKey();", "网关加密层从 GateWayManager 取 key；报告不输出真实运行态 key。"),
    ("gateway_provider_whitelist", "return GateWayManager.getWhiteList();", "网关加密层依赖白名单决定是否加密。"),
    ("gateway_provider_version", "return GateWayManager.getXVersion();", "网关加密层提供 x-request-version。"),
    ("gateway_encrypt_header_request_id", 'builderNewBuilder.header("x-request-id", strObtainXRequestId);', "网关加密层写 x-request-id。"),
    ("gateway_encrypt_header_nonce", 'builderNewBuilder.header("X-Request-Nonce-Str", strBuildNonceStr);', "网关加密层写 nonce header。"),
    ("gateway_encrypt_header_version", 'builderNewBuilder.header("x-request-version", this.mProvider.provideXRequestVersion());', "网关加密层写 x-request-version。"),
    ("gateway_encrypt_query", "encryptQueryParams(strBuildNonceStr, originalRequest, builderNewBuilder);", "网关加密层会处理 query 参数。"),
    ("gateway_encrypt_body", "encryptBody(strBuildNonceStr, originalRequest, builderNewBuilder);", "网关加密层会处理 body。"),
    ("gateway_whitelist_seed", 'hashSet.add("https://sdk-apix-secure.37.com.cn/server-info-service/get-url");', "网关白名单初始包含 server-info-service。"),
    ("gateway_whitelist_dynamic", 'if (str.contains("-secure"))', "网关白名单会吸收 secure URL。"),
    ("common_v2_pid", 'map.put("pid", sQAppConfig.getPartner());', "CommonParamsV2 写 pid。"),
    ("common_v2_gid", 'map.put("gid", sQAppConfig.getGameid());', "CommonParamsV2 写 gid。"),
    ("common_v2_refer", 'map.put("refer", sQAppConfig.getRefer());', "CommonParamsV2 写 refer。"),
    ("common_v2_dev", 'map.put("dev", DevLogic.getInstance(applicationContext).getValue());', "CommonParamsV2 写 dev。"),
    ("common_v2_sversion", 'map.put("sversion", VersionUtil.sdkVersion);', "CommonParamsV2 写 SDK 版本。"),
    ("common_v2_version", 'map.put("version", AppUtils.getVersionName(applicationContext));', "CommonParamsV2 写宿主应用版本。"),
    ("common_v2_gwversion", 'map.put("gwversion", "4.6.7");', "CommonParamsV2 写网关版本。"),
    ("common_v2_time", 'map.put("time", String.valueOf(System.currentTimeMillis() / 1000));', "CommonParamsV2 写秒级时间戳。"),
    ("common_v2_scut", "map.put(\"scut\", getCodeOfLogin(applicationContext));", "CommonParamsV2 写登录 code。"),
    ("common_v2_oaid", 'map.put("oaid", oaid);', "CommonParamsV2 写 oaid 字段名。"),
    ("common_v2_host_sdk_version", "map.put(SqConstants.HOST_SDK_VERSION, VersionUtil.getOriginalVersion());", "CommonParamsV2 写宿主 SDK 版本。"),
    ("common_v2_is_root", "map.put(SqConstants.IS_ROOT, RootLogic.getInstance(applicationContext).getValue());", "CommonParamsV2 写 root 标记。"),
    ("common_v2_is_simulator", "map.put(SqConstants.IS_SIMULATOR, SimulatorLogic.getInstance(applicationContext).getValue());", "CommonParamsV2 写模拟器标记。"),
    ("common_v3_pid", 'map.put("pid", partner);', "CommonParamsV3 写 pid。"),
    ("common_v3_gid", 'map.put("gid", gameid);', "CommonParamsV3 写 gid。"),
    ("common_v3_refer", 'map.put("refer", refer);', "CommonParamsV3 写 refer。"),
    ("common_v3_dev", 'map.put("dev", DevLogic.getInstance(applicationContext).getValue());', "CommonParamsV3 写 dev。"),
    ("common_v3_android_id", 'map.put("android_id", DevManager.getAndroidId', "CommonParamsV3 写 android_id。"),
    ("common_v3_version", 'map.put("version", appVersionName);', "CommonParamsV3 写宿主应用版本。"),
    ("common_v3_gwversion", 'map.put("gwversion", "4.6.7");', "CommonParamsV3 写网关版本。"),
    ("common_v3_sversion", 'map.put("sversion", VersionUtil.sdkVersion);', "CommonParamsV3 写 SDK 版本。"),
    ("common_v3_mac", 'map.put("mac", MacLogic.getInstance(applicationContext).getValue());', "CommonParamsV3 写 mac 字段名。"),
    ("common_v3_imei", 'map.put("imei", ImeiLogic.getInstance(applicationContext).getValue());', "CommonParamsV3 写 imei 字段名。"),
    ("common_v3_os", "map.put(\"os\", IMUrl.OS);", "CommonParamsV3 写 os。"),
    ("common_v3_over", 'map.put("over", Build.VERSION.RELEASE);', "CommonParamsV3 写系统版本。"),
    ("common_v3_time", 'map.put("time", String.valueOf(System.currentTimeMillis() / 1000));', "CommonParamsV3 写秒级时间戳。"),
    ("common_v3_oaid", 'map.put("oaid", oaid);', "CommonParamsV3 写 oaid 字段名。"),
    ("common_v3_host_sdk_version", "map.put(SqConstants.HOST_SDK_VERSION, VersionUtil.getOriginalVersion());", "CommonParamsV3 写宿主 SDK 版本。"),
    ("common_v3_is_root", "map.put(SqConstants.IS_ROOT, RootLogic.getInstance(applicationContext).getValue());", "CommonParamsV3 写 root 标记。"),
    ("common_v3_is_simulator", "map.put(SqConstants.IS_SIMULATOR, SimulatorLogic.getInstance(applicationContext).getValue());", "CommonParamsV3 写模拟器标记。"),
    ("account_login_sign_v3", "SqRequest.of(UrlConstant.LOGIN).signV3()", "账号密码登录进入 signV3 请求链。"),
    ("account_login_dtoken", 'addHeader("D-Token", DevicesFingerprint.getDevToken(this.mContext))', "账号密码登录携带 D-Token header。"),
    ("account_login_fields", '.addParam("uname", str).addParam(SqConstants.UPWD, str2)', "账号密码登录字段含 uname/upwd。"),
    ("account_login_trans_info", "addParam(SqConstants.TRANS_INFO, SqRequest.getTransInfo())", "账号密码登录携带 trans_info 字段名。"),
    ("account_login_common_v2", "addParamsTransformer(new CommonParamsV2())", "账号密码登录附加 CommonParamsV2。"),
    ("phone_login_pwd_common_v3", "SqRequest.of(UrlConstant.URL_LOGIN_PHONE_PWD).signV3()", "手机号密码登录进入 signV3 请求链。"),
    ("phone_login_code_common_v3", "SqRequest.of(UrlConstant.URL_LOGIN_CHECK_CODE).signV3()", "手机号验证码登录进入 signV3 请求链。"),
    ("phone_login_ticket_common_v3", "SqRequest.of(UrlConstant.URL_LOGIN_TICKET).signV3()", "Ticket 登录进入 signV3 请求链。"),
    ("wechat_login_common_v3", "SqRequest.of(UrlConstant.WECHAT_LOGIN).signV3()", "微信登录进入 signV3 请求链。"),
    ("fast_verify_common_v3", "SqRequest.of(UrlConstant.URL_VERIFY_FAST_TOKEN).signV3()", "闪验 token 校验进入 signV3 请求链。"),
    ("fast_login_common_v3", "SqRequest.of(UrlConstant.URL_LOGIN_FAST).signV3()", "快速登录进入 signV3 请求链。"),
)


def _collect_jadx_sy37_request_signing_hits(export_base: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    java_files = sorted(plugin_sources_root.rglob("*.java"))
    rows: list[dict[str, object]] = []
    hit_counts: Counter[str] = Counter()
    error_marker_files: set[str] = set()
    error_marker_lines = 0

    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if "JADX ERROR" in clean_line:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            for category, needle, interpretation in _JADX_SY37_REQUEST_SIGNING_NEEDLES:
                if needle not in clean_line:
                    continue
                hit_counts[category] += 1
                rows.append(
                    {
                        "category": category,
                        "file": rel_path,
                        "line": line_no,
                        "needle": needle,
                        "text": clean_line,
                        "interpretation": interpretation,
                    }
                )

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    common_v2_fields = (
        "common_v2_pid",
        "common_v2_gid",
        "common_v2_refer",
        "common_v2_dev",
        "common_v2_sversion",
        "common_v2_version",
        "common_v2_gwversion",
        "common_v2_time",
        "common_v2_scut",
        "common_v2_oaid",
        "common_v2_host_sdk_version",
        "common_v2_is_root",
        "common_v2_is_simulator",
    )
    common_v3_fields = (
        "common_v3_pid",
        "common_v3_gid",
        "common_v3_refer",
        "common_v3_dev",
        "common_v3_android_id",
        "common_v3_version",
        "common_v3_gwversion",
        "common_v3_sversion",
        "common_v3_mac",
        "common_v3_imei",
        "common_v3_os",
        "common_v3_over",
        "common_v3_time",
        "common_v3_oaid",
        "common_v3_host_sdk_version",
        "common_v3_is_root",
        "common_v3_is_simulator",
    )

    verdict: dict[str, object] = {
        "plugin_jadx_sources_present": bool(java_files),
        "sqrequest_sign_tag_found": has("sqrequest_sign_v3_tag")
        and has("sqrequest_params_transformer_api")
        and has("sqrequest_stringify_transformer"),
        "http_client_interceptor_chain_found": has("httpclient_request_id_interceptor")
        and has("httpclient_sign_interceptor")
        and has("httpclient_gateway_interceptor"),
        "sign_interceptor_dispatch_found": has("sign_interceptor_appkey_source")
        and has("sign_interceptor_v3_instance")
        and has("sign_interceptor_v3_dispatch"),
        "sign_v3_algorithm_shape_found": has("sign_v3_form_skip_existing")
        and has("sign_v3_form_add_sign")
        and has("sign_v3_get_add_sign")
        and has("sign_v3_sort_keys")
        and has("sign_v3_md5_lower"),
        "request_id_headers_found": has("request_id_headers") and has("request_liveid_header"),
        "gateway_encryption_layer_present": has("gateway_provider_key")
        and has("gateway_provider_whitelist")
        and has("gateway_provider_version")
        and has("gateway_encrypt_header_nonce")
        and has("gateway_encrypt_query")
        and has("gateway_encrypt_body"),
        "gateway_secure_whitelist_found": has("gateway_whitelist_seed") and has("gateway_whitelist_dynamic"),
        "common_params_v2_fields_found": all(has(category) for category in common_v2_fields),
        "common_params_v3_fields_found": all(has(category) for category in common_v3_fields),
        "account_login_uses_common_params_v2": has("account_login_sign_v3")
        and has("account_login_dtoken")
        and has("account_login_fields")
        and has("account_login_trans_info")
        and has("account_login_common_v2"),
        "phone_fast_wechat_use_common_params_v3": has("phone_login_pwd_common_v3")
        and has("phone_login_code_common_v3")
        and has("phone_login_ticket_common_v3")
        and has("wechat_login_common_v3")
        and has("fast_verify_common_v3")
        and has("fast_login_common_v3"),
        "decompile_has_error_markers": bool(error_marker_files),
        "safe_static_shape_only": True,
    }
    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "hit_rows": len(rows),
        "hit_categories": dict(hit_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
        "common_v2_field_hits": sum(1 for category in common_v2_fields if has(category)),
        "common_v2_field_expected": len(common_v2_fields),
        "common_v3_field_hits": sum(1 for category in common_v3_fields if has(category)),
        "common_v3_field_expected": len(common_v3_fields),
    }
    rows.sort(key=lambda row: (str(row["file"]), str(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_sy37_request_signing_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "step": "01-request-builder",
            "source": "SqRequest.of(url).signV3()",
            "target": "RequestBuilder tag SignInterceptor.SignVersion.V3",
            "confidence": 95 if verdict.get("sqrequest_sign_tag_found") else 55,
            "note": "业务层声明请求要走 V3 签名，实际签名稍后由 OkHttp 拦截器补齐。",
        },
        {
            "step": "02-common-params",
            "source": "addParamsTransformer(CommonParamsV2/CommonParamsV3)",
            "target": "pid/gid/refer/dev/version/time/device flags",
            "confidence": 95
            if verdict.get("common_params_v2_fields_found") and verdict.get("common_params_v3_fields_found")
            else 60,
            "note": "公共参数集中在 CommonParamsV2/V3，不是各登录函数手写完整参数表。",
        },
        {
            "step": "03-login-envelope",
            "source": "AccountRequestManager/LoginRequestManager/FastLoginHttpUtil",
            "target": "D-Token + trans_info + login-specific fields",
            "confidence": 95
            if verdict.get("account_login_uses_common_params_v2")
            and verdict.get("phone_fast_wechat_use_common_params_v3")
            else 60,
            "note": "账号密码偏 V2 公共参数，手机/闪验/微信偏 V3 公共参数。",
        },
        {
            "step": "04-okhttp-chain",
            "source": "SqHttpClient.createBuilder",
            "target": "RequestIdOkInterceptor -> SignInterceptor -> GateWayEncryptInterceptor",
            "confidence": 95 if verdict.get("http_client_interceptor_chain_found") else 55,
            "note": "签名不是业务函数直接算完就发，而是在统一 HTTP 客户端链路里执行。",
        },
        {
            "step": "05-sign-dispatch",
            "source": "SignInterceptor",
            "target": "SignV3Interceptor(appKey)",
            "confidence": 95 if verdict.get("sign_interceptor_dispatch_found") else 55,
            "note": "这里记录 appKey 来源形状，不读取或固化本机真实 key。",
        },
        {
            "step": "06-sign-shape",
            "source": "SignV3Interceptor",
            "target": "sorted params -> append key -> MD5 lowercase -> sign param",
            "confidence": 95 if verdict.get("sign_v3_algorithm_shape_found") else 55,
            "note": "这是静态算法形状证据；报告不生成可直接重放请求的调用器。",
        },
        {
            "step": "07-gateway-layer",
            "source": "GateWayEncryptInterceptor",
            "target": "secure whitelist -> request headers -> encrypted query/body",
            "confidence": 90 if verdict.get("gateway_encryption_layer_present") else 55,
            "note": "命中 secure 白名单时，sign 后还可能再过一层网关加密。",
        },
    ]


def _write_jadx_sy37_request_signing_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX sy37 request signing report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        f"- Portable Java: `{_JADX_PORTABLE_JAVA_EXE}`",
        f"- Portable JADX: `{_JADX_PORTABLE_EXE}`",
        "- Scope: static request-envelope evidence only. This report records request builder tags, common parameter field names, interceptor order, and signing/encryption boundaries; it does not read local account cache, collect real token/password/device values, or generate a replay client.",
        "",
        "## Conclusion",
        "",
        "- `SqRequest.signV3()` only marks a request with `SignInterceptor.SignVersion.V3`; the actual `sign` parameter is added later by `SignInterceptor -> SignV3Interceptor` in the shared OkHttp chain.",
        "- `CommonParamsV2` and `CommonParamsV3` are the central public-parameter injectors. Account-password login uses the V2 family; phone, ticket, flash/quick, and WeChat login use the V3 family.",
        "- V3 signature shape is sorted parameter names, concatenated key/value material, appKey tail, then lowercase MD5. Existing `sign` is excluded before recomputing.",
        "- `SqHttpClient` runs `RequestIdOkInterceptor`, `SignInterceptor`, then `GateWayEncryptInterceptor`; secure gateway requests can therefore be signed and then encrypted by the gateway layer.",
        "- This is enough to understand the request envelope structure, but intentionally stops short of emitting live-request forging instructions.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| sq_plugin_java_source_count | {stats.get('sq_plugin_java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| common_v2_field_hits | {stats.get('common_v2_field_hits', 0)} / {stats.get('common_v2_field_expected', 0)} |",
            f"| common_v3_field_hits | {stats.get('common_v3_field_hits', 0)} / {stats.get('common_v3_field_expected', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=360)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(
        [
            "",
            "## Hit Categories",
            "",
            "| category | count |",
            "| --- | ---: |",
        ]
    )
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| category | source | line | text | interpretation |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:220]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''))}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_request_signing_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, meta = _collect_jadx_sy37_request_signing_hits(export_base)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_sy37_request_signing_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_sy37_request_signing_hits.tsv",
        ["category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_sy37_request_signing_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_sy37_request_signing_markdown(
        output_dir / "apk_jadx_sy37_request_signing_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_request_signing_report.md"),
            "hits": str(output_dir / "apk_jadx_sy37_request_signing_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_sy37_request_signing_flow.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_request_signing_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_request_signing_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JADX_SY37_URL_UPDATE_NEEDLES: tuple[tuple[str, str, str], ...] = (
    ("base_update_manager_create", "final UpdateUrlManager updateUrlManager = new UpdateUrlManager();", "BaseSQwanCore 初始化时创建动态 URL 管理器。"),
    ("base_update_request", "updateUrlManager.reqUrlUpdateManager(new SqHttpCallback<JSONObject>()", "BaseSQwanCore 发起动态 URL 下发请求。"),
    ("base_update_parse", "HashMap<String, String> map = updateUrlManager.parse(jSONObject);", "动态 URL 响应先解析成 api_key -> api_info。"),
    ("base_gateway_cond", "GateWayManager.getWhiteList().contains(UrlConstant.UPDATE_URL)", "只有更新接口在网关白名单中时才刷新网关 key/白名单。"),
    ("base_gateway_update_whitelist", "GateWayManager.updateWhiteList(map);", "动态 URL map 会刷新 secure 白名单。"),
    ("base_gateway_update_keyset", "GateWayManager.updateKeySet(jSONObject);", "动态 URL 响应也携带网关 keyset 字段形状。"),
    ("base_url_update_set_data", "UrlUpdateUtils.setUrlData(map, BaseSQwanCore.this.context);", "动态 URL map 会写入 UrlUpdateUtils 缓存与 SharedPreferences。"),
    ("base_protect_urls_dynamic", "ProtectUrlManager.getInstance().setProtectUrls(updateUrlManager.getApiUrls());", "动态 URL 列表会交给 HTTPDNS 保护域名管理。"),
    ("base_protect_urls_fallback", "ProtectUrlManager.getInstance().setProtectUrls(UrlUpdateUtils.getUrls());", "动态 URL 请求失败时使用本地静态 URL 列表兜底。"),
    ("base_after_api_update", "BaseSQwanCore.this.afterApiUpdate(context);", "URL 更新流程结束后才继续后续初始化。"),
    ("base_httpdns_pull", "SqHttpDns.getInstance().pullDnsConfig();", "后续初始化会拉取 HTTPDNS 配置。"),
    ("base_url_update_apply", "UrlUpdateUtils.urlUpdate(context);", "后续初始化会把缓存 URL 回写到静态字段。"),
    ("update_request_shape", "SqRequest.of(UrlConstant.UPDATE_URL).signV3().addParamsTransformer(new CommonParamsV1()).get", "动态 URL 请求本身是 signV3 + CommonParamsV1 的 GET。"),
    ("update_parse_api_infos", 'jSONObject.optJSONArray("api_infos")', "动态 URL 响应主数组字段名。"),
    ("update_parse_api_key", 'jSONObjectOptJSONObject.getString("api_key")', "动态 URL 响应条目的 key 字段名。"),
    ("update_parse_api_info", 'jSONObjectOptJSONObject.getString("api_info")', "动态 URL 响应条目的 URL/info 字段名。"),
    ("update_parse_skip_secure_key", 'if (!"x_secure_key".equals(string))', "普通 URL map 会排除 x_secure_key。"),
    ("update_parse_api_map_put", "apiMap.put(string, string2);", "普通 api_key/api_info 写入 apiMap。"),
    ("update_parse_api_urls_add", "this.apiUrls.add(string2);", "普通 api_info 同时进入保护 URL 列表。"),
    ("urlconstant_update_url", 'UPDATE_URL = "https://sdk-apix-secure."', "默认动态 URL 下发接口。"),
    ("gateway_seed_update_url", 'hashSet.add("https://sdk-apix-secure.37.com.cn/server-info-service/get-url");', "网关白名单默认种子是 URL 下发接口。"),
    ("gateway_update_whitelist_clear", "sWhiteList.clear();", "刷新网关白名单时先清空旧列表。"),
    ("gateway_secure_filter", 'if (str.contains("-secure"))', "只有包含 -secure 的 URL 进入网关白名单。"),
    ("gateway_update_keyset_entry", "public static void updateKeySet(JSONObject jSONObject)", "GateWayManager 负责解析网关 keyset。"),
    ("gateway_find_secure_key", 'if ("x_secure_key".equals(jSONObjectOptJSONObject.optString("api_key")))', "x_secure_key 在 api_infos 中单独解析。"),
    ("gateway_key_appkey_field", 'jSONObject2.optString("X-Request-AppKey")', "网关 keyset 字段名：X-Request-AppKey。"),
    ("gateway_key_secret_field", 'jSONObject2.optString("X-Request-AppSecret")', "网关 keyset 字段名：X-Request-AppSecret。"),
    ("gateway_key_version_field", 'jSONObject2.optString("X-Request-Version")', "网关 keyset 字段名：X-Request-Version。"),
    ("gateway_fixed_key_save", "saveKey(GateWayUtils.getFixedKey(strOptString, strOptString2));", "运行态网关 key 会派生后保存；报告不输出真实值。"),
    ("gateway_version_save", "saveXVersion(strOptString3);", "网关版本也会落本地 SharedPreferences。"),
    ("urlupdate_annotation_interface", "public @interface UrlUpdate", "UrlUpdate 是运行时保留的字段注解。"),
    ("urlupdate_annotation_value", "String value();", "UrlUpdate 默认 key。"),
    ("urlupdate_annotation_xvalue", 'String xValue() default "";', "UrlUpdate 可选 xValue key。"),
    ("urlupdateutils_sp_name", 'private static final String SQ_URL_UPDATE = "sq_url_update";', "URL 更新落盘 SharedPreferences 名。"),
    ("urlupdateutils_urlmod_iter", "UrlMod.getUrlMod().entrySet().iterator()", "UrlUpdateUtils 遍历 UrlMod 注册的 URL 类。"),
    ("urlupdateutils_class_for_name", "Class<?> cls = Class.forName(it.next().getValue());", "URL 类通过反射加载。"),
    ("urlupdateutils_field_annotation", "(UrlUpdate) field.getAnnotation(UrlUpdate.class)", "URL 字段通过 UrlUpdate 注解识别。"),
    ("urlupdateutils_xvalue_prefer", "if (urlMap.get(urlUpdate.xValue()) != null)", "优先使用 xValue 命中的动态 URL。"),
    ("urlupdateutils_value_fallback", "strValue = urlUpdate.value();", "xValue 不命中时回落 value。"),
    ("urlupdateutils_gateway_add", "GateWayManager.addWhiteListUrl((String) obj);", "静态字段旧值也会加入 secure 白名单。"),
    ("urlupdateutils_field_set", "field.set(null, urlFromSpOrLocal);", "动态 URL 最终反射写回静态字段。"),
    ("urlupdateutils_put_all", "urlMap.putAll(map);", "动态 URL map 会进入内存 urlMap。"),
    ("urlupdateutils_sp_put", "editorEdit.putString(str, str2);", "动态 URL key/value 会落盘。"),
    ("protect_default_bugless", 'arrayList.add("bugless.shan-yu-tech.com");', "默认 HTTPDNS 保护域名之一。"),
    ("protect_default_track", 'defaultProtectHosts.add("track.37.com.cn");', "默认 HTTPDNS 保护域名之一。"),
    ("protect_set_urls", "public void setProtectUrls(ArrayList<String> arrayList)", "ProtectUrlManager 接收动态 URL 列表。"),
    ("protect_extract_host", "String host = SoftwareUtils.getHost(it.next());", "保护 URL 会被抽取 host。"),
    ("protect_set_httpdns_hosts", "SqHttpDns.getInstance().setProtectHosts(this.protectHosts);", "保护 host 交给 HTTPDNS。"),
    ("urlmod_imurl", 'urlMod.put("IMUrl", "com.sqwan.msdk.api.IMUrl");', "UrlMod 注册 IMUrl。"),
    ("urlmod_inewurl", 'urlMod.put("INewUrl", "com.sy37sdk.core.INewUrl");', "UrlMod 注册 INewUrl。"),
    ("urlmod_account", 'urlMod.put("accountUrl", "com.sy37sdk.account.UrlConstant");', "UrlMod 注册账号 URL 类。"),
    ("urlmod_order", 'urlMod.put("orderUrl", "com.sy37sdk.order.OrderUrl");', "UrlMod 注册订单 URL 类。"),
    ("common_v1_gid", 'map.put("gid", gameid);', "CommonParamsV1 写 gid。"),
    ("common_v1_pid", 'map.put("pid", partner);', "CommonParamsV1 写 pid。"),
    ("common_v1_refer", 'map.put("refer", refer);', "CommonParamsV1 写 refer。"),
    ("common_v1_version", 'map.put("version", versionName);', "CommonParamsV1 写应用版本。"),
    ("common_v1_time", 'map.put("time", String.valueOf(System.currentTimeMillis() / 1000));', "CommonParamsV1 写秒级时间戳。"),
    ("common_v1_dev", 'map.put("dev", DevLogic.getInstance(applicationContext).getValue());', "CommonParamsV1 写 dev。"),
    ("common_v1_oaid", 'map.put("oaid", oaid);', "CommonParamsV1 写 oaid 字段名。"),
    ("common_v1_sversion", 'map.put("sversion", VersionUtil.sdkVersion);', "CommonParamsV1 写 SDK 版本。"),
    ("common_v1_gwversion", 'map.put("gwversion", "4.6.7");', "CommonParamsV1 写网关版本。"),
    ("common_v1_is_root", "map.put(SqConstants.IS_ROOT, RootLogic.getInstance(applicationContext).getValue());", "CommonParamsV1 写 root 标记。"),
    ("common_v1_is_simulator", "map.put(SqConstants.IS_SIMULATOR, SimulatorLogic.getInstance(applicationContext).getValue());", "CommonParamsV1 写模拟器标记。"),
)


def _collect_jadx_sy37_url_update_hits(export_base: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    java_files = sorted(plugin_sources_root.rglob("*.java"))
    rows: list[dict[str, object]] = []
    hit_counts: Counter[str] = Counter()
    error_marker_files: set[str] = set()
    error_marker_lines = 0
    annotation_rows = 0

    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            clean_line = line.strip()
            if "JADX ERROR" in clean_line:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            if clean_line.startswith("@UrlUpdate"):
                annotation_rows += 1
                hit_counts["url_update_annotation_use"] += 1
                rows.append(
                    {
                        "category": "url_update_annotation_use",
                        "file": rel_path,
                        "line": line_no,
                        "needle": "@UrlUpdate",
                        "text": clean_line,
                        "interpretation": "动态 URL 可反射更新的静态字段注解。",
                    }
                )
            for category, needle, interpretation in _JADX_SY37_URL_UPDATE_NEEDLES:
                if needle not in clean_line:
                    continue
                hit_counts[category] += 1
                rows.append(
                    {
                        "category": category,
                        "file": rel_path,
                        "line": line_no,
                        "needle": needle,
                        "text": clean_line,
                        "interpretation": interpretation,
                    }
                )

    def has(category: str) -> bool:
        return hit_counts.get(category, 0) > 0

    common_v1_fields = (
        "common_v1_gid",
        "common_v1_pid",
        "common_v1_refer",
        "common_v1_version",
        "common_v1_time",
        "common_v1_dev",
        "common_v1_oaid",
        "common_v1_sversion",
        "common_v1_gwversion",
        "common_v1_is_root",
        "common_v1_is_simulator",
    )

    verdict: dict[str, object] = {
        "plugin_jadx_sources_present": bool(java_files),
        "server_info_request_found": has("base_update_manager_create")
        and has("base_update_request")
        and has("update_request_shape")
        and has("urlconstant_update_url"),
        "server_info_response_parse_found": has("base_update_parse")
        and has("update_parse_api_infos")
        and has("update_parse_api_key")
        and has("update_parse_api_info")
        and has("update_parse_skip_secure_key")
        and has("update_parse_api_map_put")
        and has("update_parse_api_urls_add"),
        "url_reflection_update_found": has("base_url_update_set_data")
        and has("base_url_update_apply")
        and has("urlupdate_annotation_interface")
        and has("urlupdateutils_urlmod_iter")
        and has("urlupdateutils_field_annotation")
        and has("urlupdateutils_xvalue_prefer")
        and has("urlupdateutils_field_set"),
        "gateway_keyset_update_found": has("base_gateway_cond")
        and has("base_gateway_update_keyset")
        and has("gateway_update_keyset_entry")
        and has("gateway_find_secure_key")
        and has("gateway_key_appkey_field")
        and has("gateway_key_secret_field")
        and has("gateway_key_version_field")
        and has("gateway_fixed_key_save")
        and has("gateway_version_save"),
        "gateway_whitelist_update_found": has("base_gateway_update_whitelist")
        and has("gateway_seed_update_url")
        and has("gateway_update_whitelist_clear")
        and has("gateway_secure_filter"),
        "httpdns_protect_urls_found": has("base_protect_urls_dynamic")
        and has("base_protect_urls_fallback")
        and has("protect_set_urls")
        and has("protect_extract_host")
        and has("protect_set_httpdns_hosts"),
        "urlmod_registry_found": has("urlmod_imurl")
        and has("urlmod_inewurl")
        and has("urlmod_account")
        and has("urlmod_order"),
        "common_params_v1_fields_found": all(has(category) for category in common_v1_fields),
        "url_update_annotations_found": annotation_rows > 0,
        "decompile_has_error_markers": bool(error_marker_files),
        "safe_static_shape_only": True,
    }
    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "hit_rows": len(rows),
        "url_update_annotation_rows": annotation_rows,
        "hit_categories": dict(hit_counts),
        "common_v1_field_hits": sum(1 for category in common_v1_fields if has(category)),
        "common_v1_field_expected": len(common_v1_fields),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    rows.sort(key=lambda row: (str(row["file"]), str(row["line"]), str(row["category"])))
    return rows, {"verdict": verdict, "stats": stats}


def _build_jadx_sy37_url_update_flow(verdict: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "step": "01-init-url-update-request",
            "source": "BaseSQwanCore.init",
            "target": "UpdateUrlManager.reqUrlUpdateManager",
            "confidence": 95 if verdict.get("server_info_request_found") else 55,
            "note": "SDK 初始化早期请求 server-info/get-url，后续流程等它成功或失败后继续。",
        },
        {
            "step": "02-request-envelope",
            "source": "UpdateUrlManager.reqUrlUpdateManager",
            "target": "SqRequest.of(UrlConstant.UPDATE_URL).signV3().CommonParamsV1().GET",
            "confidence": 95 if verdict.get("server_info_request_found") else 55,
            "note": "URL 下发请求本身也走前一份报告确认的 signV3 请求信封。",
        },
        {
            "step": "03-api-infos-parse",
            "source": "UpdateUrlManager.parse",
            "target": "api_infos api_key/api_info -> apiMap/apiUrls; x_secure_key excluded",
            "confidence": 95 if verdict.get("server_info_response_parse_found") else 55,
            "note": "普通 URL 和 x_secure_key 在解析时分流。",
        },
        {
            "step": "04-gateway-refresh",
            "source": "BaseSQwanCore.onSuccess",
            "target": "GateWayManager.updateWhiteList/updateKeySet",
            "confidence": 95
            if verdict.get("gateway_keyset_update_found") and verdict.get("gateway_whitelist_update_found")
            else 55,
            "note": "只记录 keyset 字段名和派生/保存边界，不输出真实 key 值。",
        },
        {
            "step": "05-reflect-url-fields",
            "source": "UrlUpdateUtils.setUrlData/urlUpdate",
            "target": "@UrlUpdate static URL fields",
            "confidence": 95 if verdict.get("url_reflection_update_found") else 55,
            "note": "UrlMod 注册 URL 类，UrlUpdate 注解决定 api_key/xValue 到静态字段的映射。",
        },
        {
            "step": "06-httpdns-protect-hosts",
            "source": "ProtectUrlManager",
            "target": "apiUrls or local UrlUpdateUtils.getUrls -> SqHttpDns protect hosts",
            "confidence": 90 if verdict.get("httpdns_protect_urls_found") else 55,
            "note": "动态 URL 同时形成 HTTPDNS 保护域名集合；失败时使用本地静态 URL 兜底。",
        },
    ]


def _write_jadx_sy37_url_update_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    category_counts = Counter(str(row.get("category", "")) for row in rows)
    lines = [
        "# JADX sy37 URL update report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        f"- Portable Java: `{_JADX_PORTABLE_JAVA_EXE}`",
        f"- Portable JADX: `{_JADX_PORTABLE_EXE}`",
        "- Scope: static URL-update and gateway-configuration evidence only. This report records class boundaries, field names, annotation routes, and request/update flow; it does not request the live server-info endpoint, read SharedPreferences, or output runtime gateway key values.",
        "",
        "## Conclusion",
        "",
        "- `BaseSQwanCore.init` requests `UrlConstant.UPDATE_URL` through `UpdateUrlManager.reqUrlUpdateManager`, using `signV3` plus `CommonParamsV1`.",
        "- The response shape is `api_infos[]` with `api_key/api_info`; ordinary entries populate `apiMap/apiUrls`, while `x_secure_key` is handled separately by `GateWayManager.updateKeySet`.",
        "- `UrlUpdateUtils` walks URL classes from `UrlMod`, finds static fields annotated with `@UrlUpdate`, prefers `xValue` when present, then writes the dynamic URL back to the static field and `sq_url_update` preferences.",
        "- Secure URLs refresh the gateway whitelist and HTTPDNS protect-host list. If the server-info request fails, the SDK continues with local static URLs and default protect hosts.",
        "- This closes the static explanation for why hardcoded `s-api-secure` defaults can differ from runtime URLs without treating the report as a live key or endpoint dump.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| sq_plugin_java_source_count | {stats.get('sq_plugin_java_source_count', 0)} |",
            f"| hit_rows | {stats.get('hit_rows', 0)} |",
            f"| url_update_annotation_rows | {stats.get('url_update_annotation_rows', 0)} |",
            f"| common_v1_field_hits | {stats.get('common_v1_field_hits', 0)} / {stats.get('common_v1_field_expected', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            f"| jadx_error_marker_lines | {stats.get('jadx_error_marker_lines', 0)} |",
            "",
            "## Flow",
            "",
            "| step | source | target | confidence | note |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=360)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=360)} |"
        )

    lines.extend(["", "## Hit Categories", "", "| category | count |", "| --- | ---: |"])
    for category, count in category_counts.most_common():
        lines.append(f"| `{_markdown_table_cell(category)}` | {count} |")

    lines.extend(
        [
            "",
            "## Evidence",
            "",
            "| category | source | line | text | interpretation |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in rows[:260]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('category', ''))}` | "
            f"`{_markdown_table_cell(row.get('file', ''))}` | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=420)}` | "
            f"{_markdown_table_cell(row.get('interpretation', ''), limit=360)} |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_url_update_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, meta = _collect_jadx_sy37_url_update_hits(export_base)
    verdict = dict(meta["verdict"])
    stats = dict(meta["stats"])
    flow_rows = _build_jadx_sy37_url_update_flow(verdict)

    _write_tsv(
        output_dir / "apk_jadx_sy37_url_update_hits.tsv",
        ["category", "file", "line", "needle", "text", "interpretation"],
        rows,
    )
    _write_tsv(
        output_dir / "apk_jadx_sy37_url_update_flow.tsv",
        ["step", "source", "target", "confidence", "note"],
        flow_rows,
    )
    _write_jadx_sy37_url_update_markdown(
        output_dir / "apk_jadx_sy37_url_update_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        flow_rows=flow_rows,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_url_update_report.md"),
            "hits": str(output_dir / "apk_jadx_sy37_url_update_hits.tsv"),
            "flow": str(output_dir / "apk_jadx_sy37_url_update_flow.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_url_update_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_url_update_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_URL_UPDATE_ANNOTATION_RE = re.compile(r"@UrlUpdate\((?P<args>.*)\)")
_URL_UPDATE_FIELD_RE = re.compile(
    r"(?P<visibility>public|private|protected)?\s*static\s+(?P<final>final\s+)?String\s+"
    r"(?P<field>[A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(?P<expr>.*?))?;"
)
_URL_UPDATE_CONST_RE = re.compile(r"static\s+final\s+String\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"(?P<value>[^\"]*)\"")


def _java_package_name(lines: list[str]) -> str:
    for line in lines[:30]:
        clean = line.strip()
        if clean.startswith("package ") and clean.endswith(";"):
            return clean.removeprefix("package ").removesuffix(";").strip()
    return ""


def _split_java_annotation_args(args: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    escaped = False
    paren_depth = 0
    for char in args:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            current.append(char)
            continue
        if char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth:
            paren_depth -= 1
        if char == "," and paren_depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _strip_java_string_literal(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _resolve_java_symbol(value: str, constants: dict[str, str]) -> str:
    raw = value.strip()
    if not raw:
        return ""
    literal = _strip_java_string_literal(raw)
    if literal != raw:
        return literal
    return constants.get(raw, raw)


def _parse_url_update_annotation(args: str, constants: dict[str, str]) -> dict[str, str]:
    value_raw = ""
    xvalue_raw = ""
    for part in _split_java_annotation_args(args):
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key == "value":
                value_raw = value
            elif key == "xValue":
                xvalue_raw = value
        elif not value_raw:
            value_raw = part.strip()
    value = _resolve_java_symbol(value_raw, constants)
    xvalue = _resolve_java_symbol(xvalue_raw, constants)
    return {
        "annotation_value_raw": value_raw,
        "annotation_xvalue_raw": xvalue_raw,
        "url_key": value,
        "x_key": xvalue,
        "preferred_dynamic_key": xvalue or value,
    }


def _url_expr_scheme(expr: str) -> str:
    lowered = expr.lower()
    if "https://" in lowered:
        return "https"
    if "http://" in lowered:
        return "http"
    if "wss://" in lowered:
        return "wss"
    if "ws://" in lowered:
        return "ws"
    return ""


def _url_expr_family(expr: str) -> str:
    lowered = expr.lower()
    if "-secure" in lowered:
        return "secure_gateway_candidate"
    if "track." in lowered:
        return "track"
    if "push" in lowered or "websocket" in lowered or "comet" in lowered:
        return "push_or_websocket"
    if "pay" in lowered or "order" in lowered:
        return "payment_or_order"
    if "live" in lowered:
        return "live_show"
    if "plugin" in lowered or "hotfix" in lowered:
        return "plugin_or_hotfix"
    if "api." in lowered or "-api" in lowered:
        return "api"
    if "http" in lowered or "ws" in lowered:
        return "plain_endpoint"
    return "computed_or_unknown"


def _find_java_field_assignment(lines: list[str], field_name: str, declaration_line_index: int) -> tuple[str, int]:
    pattern = re.compile(rf"(?<![\w.]){re.escape(field_name)}\s*=\s*(?P<expr>.*?);")
    for index, line in enumerate(lines):
        if index == declaration_line_index:
            continue
        match = pattern.search(line.strip())
        if match:
            return match.group("expr").strip(), index + 1
    return "", 0


def _collect_jadx_sy37_url_catalog_rows(export_base: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    rows: list[dict[str, object]] = []
    error_marker_files: set[str] = set()
    error_marker_lines = 0
    java_files = sorted(plugin_sources_root.rglob("*.java"))

    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        constants = {
            match.group("name"): match.group("value")
            for line in lines
            if (match := _URL_UPDATE_CONST_RE.search(line.strip()))
        }
        package_name = _java_package_name(lines)
        class_name = f"{package_name}.{java_path.stem}" if package_name else java_path.stem
        for line_no, line in enumerate(lines, start=1):
            clean = line.strip()
            if "JADX ERROR" in clean:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            annotation_match = _URL_UPDATE_ANNOTATION_RE.search(clean)
            if not annotation_match:
                continue
            field_match: re.Match[str] | None = None
            field_line_index = -1
            for lookahead in range(line_no, min(line_no + 8, len(lines))):
                field_match = _URL_UPDATE_FIELD_RE.search(lines[lookahead].strip())
                if field_match:
                    field_line_index = lookahead
                    break
            if not field_match:
                rows.append(
                    {
                        "class_name": class_name,
                        "file": rel_path,
                        "annotation_line": line_no,
                        "field_line": "",
                        "field": "",
                        "visibility": "",
                        "is_final": "",
                        "url_key": "",
                        "x_key": "",
                        "preferred_dynamic_key": "",
                        "annotation_value_raw": annotation_match.group("args"),
                        "annotation_xvalue_raw": "",
                        "inline_expr": "",
                        "assignment_expr": "",
                        "assignment_line": "",
                        "effective_expr": "",
                        "scheme": "",
                        "url_family": "unparsed_annotation",
                        "secure_like": False,
                        "dynamic_candidate": True,
                        "note": "UrlUpdate annotation found, but the following field was not parsed by the static catalog parser.",
                    }
                )
                continue
            annotation = _parse_url_update_annotation(annotation_match.group("args"), constants)
            field_name = field_match.group("field")
            inline_expr = (field_match.group("expr") or "").strip()
            assignment_expr, assignment_line = _find_java_field_assignment(lines, field_name, field_line_index)
            effective_expr = assignment_expr if assignment_expr and inline_expr in {"", "null"} else inline_expr
            if not effective_expr:
                effective_expr = assignment_expr
            scheme = _url_expr_scheme(effective_expr)
            url_family = _url_expr_family(effective_expr or inline_expr or assignment_expr)
            secure_like = "-secure" in (effective_expr or inline_expr or assignment_expr).lower()
            rows.append(
                {
                    "class_name": class_name,
                    "file": rel_path,
                    "annotation_line": line_no,
                    "field_line": field_line_index + 1,
                    "field": field_name,
                    "visibility": field_match.group("visibility") or "package",
                    "is_final": bool(field_match.group("final")),
                    "url_key": annotation["url_key"],
                    "x_key": annotation["x_key"],
                    "preferred_dynamic_key": annotation["preferred_dynamic_key"],
                    "annotation_value_raw": annotation["annotation_value_raw"],
                    "annotation_xvalue_raw": annotation["annotation_xvalue_raw"],
                    "inline_expr": inline_expr,
                    "assignment_expr": assignment_expr,
                    "assignment_line": assignment_line,
                    "effective_expr": effective_expr,
                    "scheme": scheme,
                    "url_family": url_family,
                    "secure_like": secure_like,
                    "dynamic_candidate": True,
                    "note": "Runtime server-info can replace this static field through UrlUpdateUtils when api_key/xValue matches.",
                }
            )

    rows.sort(key=lambda row: (str(row["class_name"]), str(row["field"]), int(row["annotation_line"] or 0)))
    by_class: list[dict[str, object]] = []
    class_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        class_groups[str(row["class_name"])].append(row)
    for class_name, group in sorted(class_groups.items()):
        by_class.append(
            {
                "class_name": class_name,
                "file": group[0].get("file", ""),
                "url_count": len(group),
                "x_key_count": sum(1 for row in group if row.get("x_key")),
                "secure_like_count": sum(1 for row in group if row.get("secure_like")),
                "null_inline_count": sum(1 for row in group if row.get("inline_expr") == "null"),
                "assignment_recovered_count": sum(1 for row in group if row.get("assignment_expr")),
                "families": ",".join(sorted({str(row.get("url_family", "")) for row in group if row.get("url_family")})),
            }
        )

    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "url_rows": len(rows),
        "class_rows": len(by_class),
        "with_x_key": sum(1 for row in rows if row.get("x_key")),
        "secure_like_rows": sum(1 for row in rows if row.get("secure_like")),
        "inline_expr_rows": sum(1 for row in rows if row.get("inline_expr")),
        "null_inline_rows": sum(1 for row in rows if row.get("inline_expr") == "null"),
        "assignment_recovered_rows": sum(1 for row in rows if row.get("assignment_expr")),
        "unparsed_rows": sum(1 for row in rows if not row.get("field")),
        "url_family_counts": dict(Counter(str(row.get("url_family", "")) for row in rows)),
        "scheme_counts": dict(Counter(str(row.get("scheme", "")) for row in rows)),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    return rows, by_class, stats


def _write_jadx_sy37_url_catalog_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    by_class: list[dict[str, object]],
    stats: dict[str, object],
) -> None:
    lines = [
        "# JADX sy37 URL catalog report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        "- Scope: static `@UrlUpdate` catalog only. This report extracts annotated SDK URL fields and default expressions; it does not call server-info, read runtime preferences, or resolve live dynamic endpoint values.",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| url_rows | {stats.get('url_rows', 0)} |",
        f"| class_rows | {stats.get('class_rows', 0)} |",
        f"| with_x_key | {stats.get('with_x_key', 0)} |",
        f"| secure_like_rows | {stats.get('secure_like_rows', 0)} |",
        f"| null_inline_rows | {stats.get('null_inline_rows', 0)} |",
        f"| assignment_recovered_rows | {stats.get('assignment_recovered_rows', 0)} |",
        f"| unparsed_rows | {stats.get('unparsed_rows', 0)} |",
        f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
        "",
        "## Interpretation",
        "",
        "- `url_key` is the legacy/default `@UrlUpdate(value=...)` key; `x_key` is the newer preferred key when present.",
        "- `effective_expr` is the best static default expression recovered from inline field initialization or a later class assignment. It is still source expression text, not a live resolved URL.",
        "- Rows marked `secure_gateway_candidate` contain `-secure` in the static expression and are likely to enter the gateway whitelist after `UrlUpdateUtils.urlUpdate` or `GateWayManager.updateWhiteList`.",
        "- Use this catalog with `apk_jadx_sy37_url_update_report.md`: the catalog lists updateable URL fields, while that report explains how server-info rewrites them at runtime.",
        "",
        "## Class Rollup",
        "",
        "| class | urls | x_keys | secure | recovered_assignments | families |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in by_class:
        lines.append(
            f"| `{_markdown_table_cell(row.get('class_name', ''), limit=180)}` | "
            f"{row.get('url_count', 0)} | "
            f"{row.get('x_key_count', 0)} | "
            f"{row.get('secure_like_count', 0)} | "
            f"{row.get('assignment_recovered_count', 0)} | "
            f"{_markdown_table_cell(row.get('families', ''), limit=220)} |"
        )

    family_counts = stats.get("url_family_counts") if isinstance(stats.get("url_family_counts"), dict) else {}
    lines.extend(["", "## Families", "", "| family | count |", "| --- | ---: |"])
    for family, count in sorted(family_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
        lines.append(f"| `{_markdown_table_cell(family)}` | {count} |")

    lines.extend(
        [
            "",
            "## Catalog Sample",
            "",
            "| class | field | key | x_key | family | expr |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:180]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('class_name', ''), limit=160)}` | "
            f"`{_markdown_table_cell(row.get('field', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('url_key', ''), limit=100)}` | "
            f"`{_markdown_table_cell(row.get('x_key', ''), limit=100)}` | "
            f"`{_markdown_table_cell(row.get('url_family', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('effective_expr', ''), limit=320)}` |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_url_catalog_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, by_class, stats = _collect_jadx_sy37_url_catalog_rows(export_base)
    row_fields = [
        "class_name",
        "file",
        "annotation_line",
        "field_line",
        "field",
        "visibility",
        "is_final",
        "url_key",
        "x_key",
        "preferred_dynamic_key",
        "annotation_value_raw",
        "annotation_xvalue_raw",
        "inline_expr",
        "assignment_expr",
        "assignment_line",
        "effective_expr",
        "scheme",
        "url_family",
        "secure_like",
        "dynamic_candidate",
        "note",
    ]
    class_fields = [
        "class_name",
        "file",
        "url_count",
        "x_key_count",
        "secure_like_count",
        "null_inline_count",
        "assignment_recovered_count",
        "families",
    ]
    _write_tsv(output_dir / "apk_jadx_sy37_url_catalog.tsv", row_fields, rows)
    _write_tsv(output_dir / "apk_jadx_sy37_url_catalog_by_class.tsv", class_fields, by_class)
    _write_jadx_sy37_url_catalog_markdown(
        output_dir / "apk_jadx_sy37_url_catalog_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        by_class=by_class,
        stats=stats,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_url_catalog_report.md"),
            "catalog": str(output_dir / "apk_jadx_sy37_url_catalog.tsv"),
            "by_class": str(output_dir / "apk_jadx_sy37_url_catalog_by_class.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_url_catalog_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_url_catalog_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_JAVA_IMPORT_RE = re.compile(r"import\s+(?P<name>[A-Za-z_][A-Za-z0-9_.]*);")
_JAVA_STATIC_IMPORT_RE = re.compile(r"import\s+static\s+(?P<class>[A-Za-z_][A-Za-z0-9_.]*)\.(?P<field>[A-Za-z_][A-Za-z0-9_]*);")
_SQREQUEST_OF_RE = re.compile(r"SqRequest\.of\((?P<expr>[^)]*)\)")
_WRAPPER_ENDPOINT_CALL_RE = re.compile(
    r"(?<!\.)\b(?P<method>post|get)\((?P<expr>(?:INewUrl|IMUrl|UrlConstant|OrderUrl|CommonUrlConstant)\.[A-Za-z_][A-Za-z0-9_]*)\b"
)
_JAVA_PARAM_NAME_RE = re.compile(
    r"\.(?P<kind>addParam|addHeader)\((?P<name>\"(?:\\.|[^\"])*\"|[A-Za-z_][A-Za-z0-9_.]*)"
)
_JAVA_MAP_PUT_NAME_RE = re.compile(r"\b(?:map|map2)\.put\((?P<name>\"(?:\\.|[^\"])*\"|[A-Za-z_][A-Za-z0-9_.]*)")


def _java_import_map(lines: list[str]) -> dict[str, str]:
    imports: dict[str, str] = {}
    for line in lines[:120]:
        clean = line.strip()
        match = _JAVA_IMPORT_RE.match(clean)
        if match:
            fqcn = match.group("name")
            imports[fqcn.rsplit(".", 1)[-1]] = fqcn
    return imports


def _java_static_import_map(lines: list[str]) -> dict[str, tuple[str, str]]:
    imports: dict[str, tuple[str, str]] = {}
    for line in lines[:140]:
        clean = line.strip()
        match = _JAVA_STATIC_IMPORT_RE.match(clean)
        if match:
            imports[match.group("field")] = (match.group("class"), match.group("field"))
    return imports


def _java_context_window(lines: list[str], line_index: int, *, max_follow_lines: int = 6, limit: int = 5000) -> str:
    chunks: list[str] = []
    for offset in range(max_follow_lines + 1):
        index = line_index + offset
        if index >= len(lines):
            break
        if offset and re.match(r"\s*(public|private|protected)\s+", lines[index]):
            break
        chunks.append(lines[index].strip())
        text = " ".join(chunk for chunk in chunks if chunk)
        if len(text) >= limit:
            return text[:limit]
    return " ".join(chunk for chunk in chunks if chunk)[:limit]


def _java_current_method_name(lines: list[str], line_index: int, match_start: int) -> str:
    method_re = re.compile(
        r"\b(?:public|private|protected|static|final|synchronized|native|abstract)\s+"
        r"[\w<>\[\].?,\s]+\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
    )
    candidates: list[str] = []
    start = max(0, line_index - 8)
    for index in range(start, line_index + 1):
        text = lines[index]
        if index == line_index:
            text = text[:match_start]
        for match in method_re.finditer(text):
            name = match.group("name")
            if name not in {"if", "for", "while", "switch", "catch", "new"}:
                candidates.append(name)
    return candidates[-1] if candidates else ""


def _java_expr_symbol(
    expr: str,
    *,
    package_name: str,
    imports: dict[str, str],
    static_imports: dict[str, tuple[str, str]],
    catalog_by_symbol: dict[tuple[str, str], dict[str, object]],
    simple_catalog_classes: dict[str, str],
) -> tuple[str, str, str, dict[str, object] | None]:
    raw = expr.strip()
    raw = raw.removeprefix("(").removesuffix(")").strip()
    if raw in static_imports:
        class_name, field = static_imports[raw]
        return class_name, field, f"{class_name}.{field}", catalog_by_symbol.get((class_name, field))

    match = re.fullmatch(r"(?P<class>[A-Za-z_][A-Za-z0-9_.]*)\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)", raw)
    if not match:
        return "", "", "", None

    class_expr = match.group("class")
    field = match.group("field")
    class_name = class_expr
    if "." not in class_expr:
        class_name = imports.get(class_expr) or simple_catalog_classes.get(class_expr) or f"{package_name}.{class_expr}"
    resolved = catalog_by_symbol.get((class_name, field))
    if not resolved and "." in class_expr:
        resolved = catalog_by_symbol.get((class_expr, field))
    return class_name, field, f"{class_name}.{field}", resolved


def _java_http_methods(context: str) -> str:
    found: list[str] = []
    for method in ("get", "post", "put", "delete"):
        if f".{method}(" in context:
            found.append(method.upper())
    return ",".join(found)


def _java_sign_versions(context: str) -> str:
    versions = set(re.findall(r"\.signV(?P<version>[1-9])\b", context))
    versions.update(re.findall(r"SignVersion\.V(?P<version>[1-9])\b", context))
    if ".sign(" in context and not versions:
        versions.add("custom")
    return ",".join(f"V{version}" if version.isdigit() else version for version in sorted(versions))


def _java_common_param_families(context: str) -> str:
    families = set(re.findall(r"new\s+(CommonParamsV[0-9]+|SCommonParam)\s*\(", context))
    if "generateCommonParamsOfPay" in context:
        families.add("PayCommonParamsMap")
    return ",".join(sorted(families))


def _java_payload_kinds(context: str) -> str:
    kinds: list[str] = []
    checks = {
        "addParam": ".addParam(",
        "params": ".params(",
        "formParams": ".formParams(",
        "jsonParams": ".jsonParams(",
        "addParamsTransformer": ".addParamsTransformer(",
    }
    for kind, needle in checks.items():
        if needle in context:
            kinds.append(kind)
    return ",".join(kinds)


def _java_request_arg_names(context: str, *, kind: str) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for match in _JAVA_PARAM_NAME_RE.finditer(context):
        if match.group("kind") != kind:
            continue
        raw = match.group("name").strip()
        name = _strip_java_string_literal(raw)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return ",".join(names[:40])


def _classify_jadx_sy37_endpoint_usage_row(row: dict[str, object]) -> tuple[str, str]:
    source_class = str(row.get("source_class", ""))
    source_method = str(row.get("source_method", ""))
    resolved_symbol = str(row.get("resolved_symbol", ""))
    resolved_field = str(row.get("resolved_field", ""))
    url_expr = str(row.get("url_expr", ""))
    context = str(row.get("context", ""))
    if row.get("catalog_joined"):
        return "joined_url_update_catalog", "Static URL field is present in the extracted @UrlUpdate catalog."
    if resolved_field == "UPDATE_URL" or resolved_symbol.endswith(".UPDATE_URL"):
        return "bootstrap_url_update", "Bootstrap server-info endpoint; this URL seeds the later @UrlUpdate runtime rewrite layer."
    if "LiveShowUrl" in resolved_symbol:
        return "static_non_urlupdate_live_show", "Static live-show URL field that is not part of the @UrlUpdate catalog."
    if source_class.endswith(".WebRequestProxy"):
        return "webview_proxy_dynamic_get", "WebView bridge proxy accepts caller-provided URL and parameter map."
    if source_class.endswith(".AccountPopupDialogHttpUtil"):
        return "stored_or_default_login_popup_url", "Login popup URL is read from sq_prefs and falls back to UrlConstant.URL_M_LOGIN_POPUP."
    if source_class.endswith(".activebefore.ActiveBeforeRequestManager"):
        return "custom_domain_probe", "Custom domain request helper accepts a caller-provided URL and runs CommonParamsV3 GET."
    if source_class.endswith(".MRequestManager"):
        if "URL_M_INIT" in context:
            return "m_layer_init_timestamp_url", "M-layer init builds IMUrl.URL_M_INIT plus a timestamp query before SqRequest.of."
        if source_method == "uploadDeviceInfo":
            return "device_info_upload_arg_url", "Device-info uploader receives the destination URL as a method argument."
        if "URL_REPORT_MDEV" in context:
            return "mdev_report_local_var_url", "MDev report copies IMUrl.URL_REPORT_MDEV into a local variable before SqRequest.of."
        if url_expr in {"str", "str2"}:
            return "m_layer_generic_post_helper", "M-layer private/generic request helper receives URL from higher-level methods."
    if source_class.endswith(".RequestManager"):
        if source_method == "initRequst":
            return "s_layer_init_timestamp_url", "S-layer init builds INewUrl.INIT plus a timestamp query before SqRequest.of."
        if url_expr == "str":
            return "s_layer_legacy_account_post_helper", "Legacy account RequestManager helper receives INewUrl fields from public wrapper methods."
    if resolved_symbol:
        return "resolved_static_not_urlupdate", "Static field resolved from imports, but the field is not annotated with @UrlUpdate."
    return "dynamic_unresolved", "Caller-provided or computed URL expression; inspect the source method or wrapper callers."


def _collect_jadx_sy37_endpoint_usage_rows(
    export_base: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    catalog_rows, _, catalog_stats = _collect_jadx_sy37_url_catalog_rows(export_base)
    catalog_by_symbol = {(str(row.get("class_name", "")), str(row.get("field", ""))): row for row in catalog_rows}
    simple_class_counts = Counter(str(row.get("class_name", "")).rsplit(".", 1)[-1] for row in catalog_rows)
    simple_catalog_classes = {
        str(row.get("class_name", "")).rsplit(".", 1)[-1]: str(row.get("class_name", ""))
        for row in catalog_rows
        if simple_class_counts[str(row.get("class_name", "")).rsplit(".", 1)[-1]] == 1
    }

    rows: list[dict[str, object]] = []
    java_files = sorted(plugin_sources_root.rglob("*.java"))
    error_marker_files: set[str] = set()
    error_marker_lines = 0

    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        package_name = _java_package_name(lines)
        source_class = f"{package_name}.{java_path.stem}" if package_name else java_path.stem
        imports = _java_import_map(lines)
        static_imports = _java_static_import_map(lines)
        for line_index, line in enumerate(lines):
            clean = line.strip()
            if "JADX ERROR" in clean:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            for match in _SQREQUEST_OF_RE.finditer(line):
                expr = match.group("expr").strip()
                class_name, field, resolved_symbol, catalog_row = _java_expr_symbol(
                    expr,
                    package_name=package_name,
                    imports=imports,
                    static_imports=static_imports,
                    catalog_by_symbol=catalog_by_symbol,
                    simple_catalog_classes=simple_catalog_classes,
                )
                context = _java_context_window(lines, line_index)
                row = {
                    "file": rel_path,
                    "line": line_index + 1,
                    "source_class": source_class,
                    "source_method": _java_current_method_name(lines, line_index, match.start()),
                    "url_expr": expr,
                    "resolved_class": class_name,
                    "resolved_field": field,
                    "resolved_symbol": resolved_symbol,
                    "catalog_joined": bool(catalog_row),
                    "url_key": catalog_row.get("url_key", "") if catalog_row else "",
                    "x_key": catalog_row.get("x_key", "") if catalog_row else "",
                    "preferred_dynamic_key": catalog_row.get("preferred_dynamic_key", "") if catalog_row else "",
                    "url_family": catalog_row.get("url_family", "") if catalog_row else "",
                    "secure_like": bool(catalog_row.get("secure_like")) if catalog_row else False,
                    "effective_expr": catalog_row.get("effective_expr", "") if catalog_row else "",
                    "http_methods": _java_http_methods(context),
                    "sign_versions": _java_sign_versions(context),
                    "common_params": _java_common_param_families(context),
                    "payload_kinds": _java_payload_kinds(context),
                    "headers": _java_request_arg_names(context, kind="addHeader"),
                    "param_names": _java_request_arg_names(context, kind="addParam"),
                    "has_token_param": "token" in _java_request_arg_names(context, kind="addParam").split(","),
                    "has_d_token_header": "D-Token" in _java_request_arg_names(context, kind="addHeader").split(","),
                    "has_trans_info": "SqConstants.TRANS_INFO" in context or "TRANS_INFO" in context,
                    "context": context,
                }
                row["usage_category"], row["usage_note"] = _classify_jadx_sy37_endpoint_usage_row(row)
                rows.append(row)

    rows.sort(key=lambda row: (str(row.get("file", "")), int(row.get("line") or 0), str(row.get("url_expr", ""))))

    endpoint_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("resolved_symbol") or row.get("url_expr") or "")
        endpoint_groups[key].append(row)

    by_endpoint: list[dict[str, object]] = []
    for endpoint, group in endpoint_groups.items():
        first = group[0]
        by_endpoint.append(
            {
                "endpoint": endpoint,
                "callsite_count": len(group),
                "catalog_joined": any(bool(row.get("catalog_joined")) for row in group),
                "resolved_class": first.get("resolved_class", ""),
                "resolved_field": first.get("resolved_field", ""),
                "url_key": first.get("url_key", ""),
                "x_key": first.get("x_key", ""),
                "preferred_dynamic_key": first.get("preferred_dynamic_key", ""),
                "url_family": first.get("url_family", ""),
                "usage_categories": ",".join(sorted({str(row.get("usage_category", "")) for row in group if row.get("usage_category")})),
                "secure_like": any(bool(row.get("secure_like")) for row in group),
                "http_methods": ",".join(sorted({part for row in group for part in str(row.get("http_methods", "")).split(",") if part})),
                "sign_versions": ",".join(sorted({part for row in group for part in str(row.get("sign_versions", "")).split(",") if part})),
                "common_params": ",".join(sorted({part for row in group for part in str(row.get("common_params", "")).split(",") if part})),
                "payload_kinds": ",".join(sorted({part for row in group for part in str(row.get("payload_kinds", "")).split(",") if part})),
                "files": ",".join(sorted({str(row.get("file", "")) for row in group})[:8]),
                "sample_context": str(first.get("context", ""))[:700],
            }
        )
    by_endpoint.sort(key=lambda row: (-int(row.get("callsite_count") or 0), str(row.get("endpoint", ""))))

    method_counts = Counter(part for row in rows for part in str(row.get("http_methods", "")).split(",") if part)
    sign_counts = Counter(part for row in rows for part in str(row.get("sign_versions", "")).split(",") if part)
    common_param_counts = Counter(part for row in rows for part in str(row.get("common_params", "")).split(",") if part)
    payload_counts = Counter(part for row in rows for part in str(row.get("payload_kinds", "")).split(",") if part)
    usage_category_counts = Counter(str(row.get("usage_category", "")) for row in rows if row.get("usage_category"))
    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "catalog_url_rows": catalog_stats.get("url_rows", 0),
        "callsite_rows": len(rows),
        "by_endpoint_rows": len(by_endpoint),
        "joined_catalog_rows": sum(1 for row in rows if row.get("catalog_joined")),
        "unresolved_url_expr_rows": sum(1 for row in rows if not row.get("catalog_joined")),
        "direct_symbol_expr_rows": sum(1 for row in rows if row.get("resolved_symbol")),
        "variable_expr_rows": sum(1 for row in rows if not row.get("resolved_symbol")),
        "secure_like_callsite_rows": sum(1 for row in rows if row.get("secure_like")),
        "token_param_callsite_rows": sum(1 for row in rows if row.get("has_token_param")),
        "d_token_header_callsite_rows": sum(1 for row in rows if row.get("has_d_token_header")),
        "trans_info_callsite_rows": sum(1 for row in rows if row.get("has_trans_info")),
        "http_method_counts": dict(method_counts),
        "sign_version_counts": dict(sign_counts),
        "common_param_counts": dict(common_param_counts),
        "payload_kind_counts": dict(payload_counts),
        "usage_category_counts": dict(usage_category_counts),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    verdict: dict[str, object] = {
        "sqrequest_calls_found": len(rows) > 0,
        "url_catalog_joined": stats["joined_catalog_rows"] > 0,
        "login_endpoint_joined": any(str(row.get("resolved_symbol", "")).endswith(".LOGIN") for row in rows),
        "common_params_detected": bool(common_param_counts),
        "sign_versions_detected": bool(sign_counts),
        "safe_static_shape_only": True,
    }
    return rows, by_endpoint, stats, verdict


def _write_jadx_sy37_endpoint_usage_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    by_endpoint: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    lines = [
        "# JADX sy37 endpoint usage report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        "- Scope: static callsite catalog for `SqRequest.of(...)`. It does not send requests, execute signing code, decrypt payloads, or collect runtime token values.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| callsite_rows | {stats.get('callsite_rows', 0)} |",
            f"| by_endpoint_rows | {stats.get('by_endpoint_rows', 0)} |",
            f"| joined_catalog_rows | {stats.get('joined_catalog_rows', 0)} |",
            f"| unresolved_url_expr_rows | {stats.get('unresolved_url_expr_rows', 0)} |",
            f"| secure_like_callsite_rows | {stats.get('secure_like_callsite_rows', 0)} |",
            f"| token_param_callsite_rows | {stats.get('token_param_callsite_rows', 0)} |",
            f"| d_token_header_callsite_rows | {stats.get('d_token_header_callsite_rows', 0)} |",
            f"| trans_info_callsite_rows | {stats.get('trans_info_callsite_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
        ]
    )

    for title, key in (
        ("HTTP Methods", "http_method_counts"),
        ("Sign Versions", "sign_version_counts"),
        ("Common Param Families", "common_param_counts"),
        ("Payload Kinds", "payload_kind_counts"),
        ("Usage Categories", "usage_category_counts"),
    ):
        values = stats.get(key) if isinstance(stats.get(key), dict) else {}
        lines.extend(["", f"## {title}", "", "| value | count |", "| --- | ---: |"])
        for value, count in sorted(values.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"| `{_markdown_table_cell(value)}` | {count} |")

    lines.extend(
        [
            "",
            "## Endpoint Rollup",
            "",
            "| endpoint | calls | category | method | sign | params | family | key |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in by_endpoint[:160]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('endpoint', ''), limit=180)}` | "
            f"{row.get('callsite_count', 0)} | "
            f"`{_markdown_table_cell(row.get('usage_categories', ''), limit=120)}` | "
            f"`{_markdown_table_cell(row.get('http_methods', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('sign_versions', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('common_params', ''), limit=120)}` | "
            f"`{_markdown_table_cell(row.get('url_family', ''), limit=100)}` | "
            f"`{_markdown_table_cell(row.get('preferred_dynamic_key', '') or row.get('url_key', ''), limit=120)}` |"
        )

    account_rows = [
        row
        for row in rows
        if "account" in str(row.get("file", "")).lower()
        or "LOGIN" in str(row.get("resolved_field", ""))
        or "login" in str(row.get("url_expr", "")).lower()
    ]
    lines.extend(
        [
            "",
            "## Account/Login Callsite Sample",
            "",
            "| source | line | url | method | sign | params | payload | names |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in account_rows[:80]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('file', ''), limit=150)}` | "
            f"{row.get('line', '')} | "
            f"`{_markdown_table_cell(row.get('resolved_symbol') or row.get('url_expr', ''), limit=170)}` | "
            f"`{_markdown_table_cell(row.get('http_methods', ''), limit=70)}` | "
            f"`{_markdown_table_cell(row.get('sign_versions', ''), limit=70)}` | "
            f"`{_markdown_table_cell(row.get('common_params', ''), limit=110)}` | "
            f"`{_markdown_table_cell(row.get('payload_kinds', ''), limit=120)}` | "
            f"`{_markdown_table_cell(row.get('param_names', ''), limit=220)}` |"
        )

    lines.extend(
        [
            "",
            "## Callsite Sample",
            "",
            "| source | line | url_expr | category | joined | method | sign | params | context |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:120]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('file', ''), limit=150)}` | "
            f"{row.get('line', '')} | "
            f"`{_markdown_table_cell(row.get('url_expr', ''), limit=150)}` | "
            f"`{_markdown_table_cell(row.get('usage_category', ''), limit=120)}` | "
            f"`{_markdown_table_cell(row.get('catalog_joined', ''))}` | "
            f"`{_markdown_table_cell(row.get('http_methods', ''), limit=70)}` | "
            f"`{_markdown_table_cell(row.get('sign_versions', ''), limit=70)}` | "
            f"`{_markdown_table_cell(row.get('common_params', ''), limit=110)}` | "
            f"`{_markdown_table_cell(row.get('context', ''), limit=380)}` |"
        )

    error_files = stats.get("jadx_error_files_sample") or []
    if isinstance(error_files, list) and error_files:
        lines.extend(["", "## JADX Error Marker File Sample", ""])
        for file in error_files:
            lines.append(f"- `{_markdown_table_cell(file)}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_endpoint_usage_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, by_endpoint, stats, verdict = _collect_jadx_sy37_endpoint_usage_rows(export_base)
    row_fields = [
        "file",
        "line",
        "source_class",
        "source_method",
        "url_expr",
        "resolved_class",
        "resolved_field",
        "resolved_symbol",
        "catalog_joined",
        "url_key",
        "x_key",
        "preferred_dynamic_key",
        "url_family",
        "usage_category",
        "usage_note",
        "secure_like",
        "effective_expr",
        "http_methods",
        "sign_versions",
        "common_params",
        "payload_kinds",
        "headers",
        "param_names",
        "has_token_param",
        "has_d_token_header",
        "has_trans_info",
        "context",
    ]
    endpoint_fields = [
        "endpoint",
        "callsite_count",
        "catalog_joined",
        "resolved_class",
        "resolved_field",
        "url_key",
        "x_key",
        "preferred_dynamic_key",
        "url_family",
        "usage_categories",
        "secure_like",
        "http_methods",
        "sign_versions",
        "common_params",
        "payload_kinds",
        "files",
        "sample_context",
    ]
    _write_tsv(output_dir / "apk_jadx_sy37_endpoint_usage.tsv", row_fields, rows)
    _write_tsv(output_dir / "apk_jadx_sy37_endpoint_usage_by_endpoint.tsv", endpoint_fields, by_endpoint)
    _write_jadx_sy37_endpoint_usage_markdown(
        output_dir / "apk_jadx_sy37_endpoint_usage_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        by_endpoint=by_endpoint,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_endpoint_usage_report.md"),
            "callsite_usage": str(output_dir / "apk_jadx_sy37_endpoint_usage.tsv"),
            "by_endpoint": str(output_dir / "apk_jadx_sy37_endpoint_usage_by_endpoint.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_endpoint_usage_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_endpoint_usage_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _java_nearby_map_put_names(lines: list[str], line_index: int, *, lookback: int = 36) -> str:
    names: list[str] = []
    seen: set[str] = set()
    start = max(0, line_index - lookback)
    for line in lines[start : line_index + 1]:
        for match in _JAVA_MAP_PUT_NAME_RE.finditer(line):
            name = _strip_java_string_literal(match.group("name").strip())
            if name not in seen:
                seen.add(name)
                names.append(name)
    return ",".join(names[:48])


def _wrapper_helper_shape(source_class: str) -> tuple[str, str, str]:
    if source_class == "com.sy37sdk.core.RequestManager":
        return "s_layer_legacy_account_post_helper", "SCommonParam", "Legacy S-layer account RequestManager private post(str, map, signVersion, signExt, ...)."
    if source_class == "com.sqwan.msdk.api.MRequestManager":
        return "m_layer_generic_post_helper", "CommonParamsV1", "M-layer MRequestManager private post(str, map, signVersion, signExt, ...)."
    return "static_wrapper_call", "", "Static wrapper call with a URL field as the first argument."


def _collect_jadx_sy37_wrapper_endpoint_rows(
    export_base: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object], dict[str, object]]:
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)
    if not plugin_sources_root.is_dir():
        raise FanxiuResourceError(
            f"缺少 sq_plugin JADX 源码目录：{plugin_sources_root}。请先反编译 SQwan 插件 APK。"
        )

    catalog_rows, _, catalog_stats = _collect_jadx_sy37_url_catalog_rows(export_base)
    catalog_by_symbol = {(str(row.get("class_name", "")), str(row.get("field", ""))): row for row in catalog_rows}
    simple_class_counts = Counter(str(row.get("class_name", "")).rsplit(".", 1)[-1] for row in catalog_rows)
    simple_catalog_classes = {
        str(row.get("class_name", "")).rsplit(".", 1)[-1]: str(row.get("class_name", ""))
        for row in catalog_rows
        if simple_class_counts[str(row.get("class_name", "")).rsplit(".", 1)[-1]] == 1
    }

    rows: list[dict[str, object]] = []
    java_files = sorted(plugin_sources_root.rglob("*.java"))
    error_marker_files: set[str] = set()
    error_marker_lines = 0
    for java_path in java_files:
        rel_path = java_path.relative_to(plugin_sources_root).as_posix()
        try:
            lines = java_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        package_name = _java_package_name(lines)
        source_class = f"{package_name}.{java_path.stem}" if package_name else java_path.stem
        imports = _java_import_map(lines)
        static_imports = _java_static_import_map(lines)
        for line_index, line in enumerate(lines):
            if "JADX ERROR" in line:
                error_marker_files.add(rel_path)
                error_marker_lines += 1
            for match in _WRAPPER_ENDPOINT_CALL_RE.finditer(line):
                expr = match.group("expr")
                class_name, field, resolved_symbol, catalog_row = _java_expr_symbol(
                    expr,
                    package_name=package_name,
                    imports=imports,
                    static_imports=static_imports,
                    catalog_by_symbol=catalog_by_symbol,
                    simple_catalog_classes=simple_catalog_classes,
                )
                helper_category, helper_common_params, helper_note = _wrapper_helper_shape(source_class)
                context = _java_context_window(lines, line_index)
                rows.append(
                    {
                        "file": rel_path,
                        "line": line_index + 1,
                        "source_class": source_class,
                        "source_method": _java_current_method_name(lines, line_index, match.start()),
                        "wrapper_method": match.group("method").upper(),
                        "url_expr": expr,
                        "resolved_class": class_name,
                        "resolved_field": field,
                        "resolved_symbol": resolved_symbol,
                        "catalog_joined": bool(catalog_row),
                        "url_key": catalog_row.get("url_key", "") if catalog_row else "",
                        "x_key": catalog_row.get("x_key", "") if catalog_row else "",
                        "preferred_dynamic_key": catalog_row.get("preferred_dynamic_key", "") if catalog_row else "",
                        "url_family": catalog_row.get("url_family", "") if catalog_row else "",
                        "secure_like": bool(catalog_row.get("secure_like")) if catalog_row else False,
                        "effective_expr": catalog_row.get("effective_expr", "") if catalog_row else "",
                        "sign_versions": _java_sign_versions(context),
                        "helper_category": helper_category,
                        "helper_common_params": helper_common_params,
                        "helper_note": helper_note,
                        "map_param_names": _java_nearby_map_put_names(lines, line_index),
                        "context": context,
                    }
                )

    rows.sort(key=lambda row: (str(row.get("file", "")), int(row.get("line") or 0), str(row.get("url_expr", ""))))
    by_helper: list[dict[str, object]] = []
    helper_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        helper_groups[str(row.get("helper_category", ""))].append(row)
    for helper_category, group in sorted(helper_groups.items()):
        by_helper.append(
            {
                "helper_category": helper_category,
                "callsite_count": len(group),
                "joined_catalog_rows": sum(1 for row in group if row.get("catalog_joined")),
                "secure_like_rows": sum(1 for row in group if row.get("secure_like")),
                "common_params": ",".join(sorted({str(row.get("helper_common_params", "")) for row in group if row.get("helper_common_params")})),
                "sign_versions": ",".join(sorted({part for row in group for part in str(row.get("sign_versions", "")).split(",") if part})),
                "sample_symbols": ",".join(str(row.get("resolved_symbol") or row.get("url_expr")) for row in group[:8]),
            }
        )

    helper_counts = Counter(str(row.get("helper_category", "")) for row in rows)
    stats: dict[str, object] = {
        "sq_plugin_java_source_count": len(java_files),
        "catalog_url_rows": catalog_stats.get("url_rows", 0),
        "wrapper_rows": len(rows),
        "helper_rows": len(by_helper),
        "joined_catalog_rows": sum(1 for row in rows if row.get("catalog_joined")),
        "secure_like_rows": sum(1 for row in rows if row.get("secure_like")),
        "helper_category_counts": dict(helper_counts),
        "sign_version_counts": dict(Counter(part for row in rows for part in str(row.get("sign_versions", "")).split(",") if part)),
        "jadx_error_marker_files": len(error_marker_files),
        "jadx_error_marker_lines": error_marker_lines,
        "jadx_error_files_sample": sorted(error_marker_files)[:20],
    }
    verdict: dict[str, object] = {
        "wrapper_calls_found": len(rows) > 0,
        "wrapper_calls_join_catalog": stats["joined_catalog_rows"] > 0,
        "s_layer_legacy_wrappers_found": bool(helper_counts.get("s_layer_legacy_account_post_helper")),
        "m_layer_wrappers_found": bool(helper_counts.get("m_layer_generic_post_helper")),
        "safe_static_shape_only": True,
    }
    return rows, by_helper, stats, verdict


def _write_jadx_sy37_wrapper_endpoint_markdown(
    path: Path,
    *,
    root: Path,
    export_base: Path,
    plugin_sources_root: Path,
    rows: list[dict[str, object]],
    by_helper: list[dict[str, object]],
    stats: dict[str, object],
    verdict: dict[str, object],
) -> None:
    lines = [
        "# JADX sy37 wrapper endpoint report",
        "",
        f"- APK unpacked root: `{root}`",
        f"- Export root: `{export_base}`",
        f"- SQ plugin JADX sources: `{plugin_sources_root}`",
        "- Scope: static wrapper-call catalog for higher-level `post(INewUrl.*)` / `post(IMUrl.*)` calls that eventually flow into generic `SqRequest.of(str)` helpers. It does not send requests or execute signing code.",
        "",
        "## Verdict",
        "",
        "| check | value |",
        "| --- | --- |",
    ]
    for key, value in verdict.items():
        lines.append(f"| `{_markdown_table_cell(key)}` | `{_markdown_table_cell(value)}` |")

    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| wrapper_rows | {stats.get('wrapper_rows', 0)} |",
            f"| helper_rows | {stats.get('helper_rows', 0)} |",
            f"| joined_catalog_rows | {stats.get('joined_catalog_rows', 0)} |",
            f"| secure_like_rows | {stats.get('secure_like_rows', 0)} |",
            f"| jadx_error_marker_files | {stats.get('jadx_error_marker_files', 0)} |",
            "",
            "## Helper Rollup",
            "",
            "| helper | calls | joined | secure | params | sign | sample symbols |",
            "| --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in by_helper:
        lines.append(
            f"| `{_markdown_table_cell(row.get('helper_category', ''), limit=120)}` | "
            f"{row.get('callsite_count', 0)} | "
            f"{row.get('joined_catalog_rows', 0)} | "
            f"{row.get('secure_like_rows', 0)} | "
            f"`{_markdown_table_cell(row.get('common_params', ''), limit=100)}` | "
            f"`{_markdown_table_cell(row.get('sign_versions', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('sample_symbols', ''), limit=260)}` |"
        )

    lines.extend(
        [
            "",
            "## Wrapper Calls",
            "",
            "| source | line | wrapper | url | joined | sign | helper params | map keys |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:160]:
        lines.append(
            f"| `{_markdown_table_cell(row.get('file', ''), limit=150)}` | "
            f"{row.get('line', '')} | "
            f"`{_markdown_table_cell(row.get('helper_category', ''), limit=120)}` | "
            f"`{_markdown_table_cell(row.get('resolved_symbol') or row.get('url_expr', ''), limit=180)}` | "
            f"`{_markdown_table_cell(row.get('catalog_joined', ''))}` | "
            f"`{_markdown_table_cell(row.get('sign_versions', ''), limit=80)}` | "
            f"`{_markdown_table_cell(row.get('helper_common_params', ''), limit=100)}` | "
            f"`{_markdown_table_cell(row.get('map_param_names', ''), limit=260)}` |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_jadx_sy37_wrapper_endpoint_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plugin_sources_root = _fanxiu_jadx_sq_plugin_sources_root(export_base)

    rows, by_helper, stats, verdict = _collect_jadx_sy37_wrapper_endpoint_rows(export_base)
    row_fields = [
        "file",
        "line",
        "source_class",
        "source_method",
        "wrapper_method",
        "url_expr",
        "resolved_class",
        "resolved_field",
        "resolved_symbol",
        "catalog_joined",
        "url_key",
        "x_key",
        "preferred_dynamic_key",
        "url_family",
        "secure_like",
        "effective_expr",
        "sign_versions",
        "helper_category",
        "helper_common_params",
        "helper_note",
        "map_param_names",
        "context",
    ]
    helper_fields = [
        "helper_category",
        "callsite_count",
        "joined_catalog_rows",
        "secure_like_rows",
        "common_params",
        "sign_versions",
        "sample_symbols",
    ]
    _write_tsv(output_dir / "apk_jadx_sy37_wrapper_endpoint.tsv", row_fields, rows)
    _write_tsv(output_dir / "apk_jadx_sy37_wrapper_endpoint_by_helper.tsv", helper_fields, by_helper)
    _write_jadx_sy37_wrapper_endpoint_markdown(
        output_dir / "apk_jadx_sy37_wrapper_endpoint_report.md",
        root=root,
        export_base=export_base,
        plugin_sources_root=plugin_sources_root,
        rows=rows,
        by_helper=by_helper,
        stats=stats,
        verdict=verdict,
    )
    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "plugin_sources_root": str(plugin_sources_root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": stats,
        "verdict": verdict,
        "outputs": {
            "markdown": str(output_dir / "apk_jadx_sy37_wrapper_endpoint_report.md"),
            "wrapper_endpoint": str(output_dir / "apk_jadx_sy37_wrapper_endpoint.tsv"),
            "by_helper": str(output_dir / "apk_jadx_sy37_wrapper_endpoint_by_helper.tsv"),
            "json": str(output_dir / "apk_jadx_sy37_wrapper_endpoint_report.json"),
        },
    }
    (output_dir / "apk_jadx_sy37_wrapper_endpoint_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _unity_login_receiver_method_role(row: dict[str, str]) -> str:
    owner = row.get("owner", "")
    name = row.get("name", "")
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name == "OnReceiveLogin":
        return "unity_login_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name == "OnReceiveLoginData":
        return "unity_login_data_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name == "OnReceiveCancelLogin":
        return "unity_cancel_login_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name.startswith("OnReceive"):
        return "unity_phone_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver":
        return "phone_receiver_method"
    if owner == "GameEnter":
        return "game_enter_lifecycle"
    return "related_method"


def _collect_unity_login_receiver_rows(output_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    type_terms = {"GameEnter", "PhoneReceiver.PhoneMsgReceiver"}
    method_terms = {"OnReceiveLogin", "OnReceiveLoginData", "OnReceiveCancelLogin"}
    string_terms = ("GameEnter", "OnReceiveLogin", "OnReceiveLoginData", "OnReceiveCancelLogin")
    type_rows: list[dict[str, object]] = []
    method_rows: list[dict[str, object]] = []
    string_rows: list[dict[str, object]] = []

    for row in _read_tsv_rows(output_dir / "il2cpp_types.tsv"):
        full_name = row.get("full_name", "")
        if full_name in type_terms:
            type_rows.append(
                {
                    "index": row.get("index", ""),
                    "full_name": full_name,
                    "method_start": row.get("method_start", ""),
                    "method_count": row.get("method_count", ""),
                    "field_start": row.get("field_start", ""),
                    "field_count": row.get("field_count", ""),
                    "token": row.get("token", ""),
                    "note": "UnitySendMessage 的 GameObject/组件候选类型；metadata 只能证明类型和方法名。",
                }
            )

    for row in _read_tsv_rows(output_dir / "il2cpp_methods.tsv"):
        owner = row.get("owner", "")
        name = row.get("name", "")
        qualified_name = row.get("qualified_name", "")
        if owner in type_terms or name in method_terms or any(term in qualified_name for term in method_terms):
            method_rows.append(
                {
                    "role": _unity_login_receiver_method_role(row),
                    "owner": owner,
                    "name": name,
                    "qualified_name": qualified_name,
                    "parameters": row.get("parameters", ""),
                    "return_type": row.get("return_type_name", "") or row.get("return_type", ""),
                    "token": row.get("token", ""),
                    "note": "IL2CPP metadata 方法签名；方法体仍需 IL2CPP 反编译才能继续追内部解析。",
                }
            )

    for table, index_field in (("il2cpp_strings.tsv", "string_index"), ("il2cpp_string_literals.tsv", "index")):
        for row in _read_tsv_rows(output_dir / table):
            value = row.get("value", "")
            matched_terms = [term for term in string_terms if term in value]
            if not matched_terms:
                continue
            string_rows.append(
                {
                    "source_table": table,
                    "index": row.get(index_field, ""),
                    "term": ",".join(matched_terms),
                    "value": value,
                    "note": "Unity 消息名或接收对象相关字符串。",
                }
            )
    type_rows.sort(key=lambda row: str(row.get("full_name", "")))
    method_rows.sort(key=lambda row: (_DEX_LOGIN_SURFACE_CATEGORY_ORDER.get(str(row.get("role", "")), 99), str(row.get("qualified_name", ""))))
    string_rows.sort(key=lambda row: (str(row.get("source_table", "")), int(str(row.get("index") or "0"))))
    return type_rows, method_rows, string_rows


def _build_unity_login_receiver_flow(
    output_dir: Path,
    method_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    dex_flow = _read_tsv_rows(output_dir / "apk_dex_login_body_flow.tsv")
    method_names = {str(row.get("qualified_name", "")) for row in method_rows}
    string_values = {str(row.get("value", "")) for row in string_rows}

    def has_dex_target(text: str) -> bool:
        needle = text.lower()
        return any(needle in f"{row.get('source', '')} {row.get('target', '')}".lower() for row in dex_flow)

    return [
        {
            "step": "01-java-unitysendmessage",
            "source": "com.flamePhoenix.plugin.plugin.SDKCallback.SendDataToUnity",
            "target": "UnityPlayer.UnitySendMessage(GameEnter, OnReceiveLogin, data)",
            "confidence": 95 if has_dex_target("UnitySendMessage") else 70,
            "evidence": "apk_dex_login_body_flow.tsv / apk_dex_login_body_refs.tsv",
            "note": "Java SDKCallback 已确认把登录消息发给 GameEnter。",
        },
        {
            "step": "02-gameobject-type",
            "source": "Unity message target",
            "target": "GameEnter",
            "confidence": 90 if any("GameEnter" == value or "GameEnter/" in value for value in string_values) else 75,
            "evidence": "il2cpp_types.tsv / il2cpp_strings.tsv",
            "note": "IL2CPP metadata 存在 GameEnter 类型和 GameEnter 相关字符串。",
        },
        {
            "step": "03-component-receiver",
            "source": "GameEnter component candidates",
            "target": "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin(data:string)",
            "confidence": 90 if "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin" in method_names else 60,
            "evidence": "il2cpp_methods.tsv",
            "note": "UnitySendMessage 会在目标 GameObject 的组件上查找同名方法；metadata 中接收方法位于 PhoneMsgReceiver。",
        },
        {
            "step": "04-cancel-login",
            "source": "OnReceiveCancelLogin",
            "target": "PhoneReceiver.PhoneMsgReceiver.OnReceiveCancelLogin(data:string)",
            "confidence": 90 if "PhoneReceiver.PhoneMsgReceiver.OnReceiveCancelLogin" in method_names else 60,
            "evidence": "il2cpp_methods.tsv",
            "note": "取消登录消息有同名 Unity 接收方法。",
        },
        {
            "step": "05-boundary",
            "source": "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin",
            "target": "method body / Lua login model",
            "confidence": 50,
            "evidence": "metadata lacks IL2CPP method body",
            "note": "继续看 data 如何拆分 token/gid/pid、如何调用 Lua 登录，需要 IL2CPP 方法体反编译。",
        },
    ]


def _write_unity_login_receiver_markdown(
    path: Path,
    *,
    root: Path,
    output_dir: Path,
    flow_rows: list[dict[str, object]],
    type_rows: list[dict[str, object]],
    method_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
) -> None:
    role_counts = Counter(str(row.get("role", "")) for row in method_rows)
    lines = [
        "# 凡修 Unity 登录接收点探针",
        "",
        f"- APK 解包目录：`{root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告合并 DEX 方法体轻量探针和 IL2CPP metadata；不反编译 IL2CPP 方法体。",
        "",
        "## 结论",
        "",
        "- Java 登录成功链路已接到 Unity：`UnitySendMessage(GameEnter, OnReceiveLogin, data)`。",
        "- IL2CPP metadata 中存在 `GameEnter` 类型，以及 `PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin(data:string)` / `OnReceiveCancelLogin(data:string)` 接收方法。",
        "- 这说明 `GameEnter` 更可能是 UnitySendMessage 的 GameObject 名，真正处理登录消息的是挂在该对象上的 `PhoneMsgReceiver` 组件。",
        "- 当前边界：metadata 无方法体，不能继续确认 `OnReceiveLogin` 如何拆 `1__token__gid__pid` 并下传到 Lua 登录模型；这一步需要 IL2CPP 反编译。",
        "",
        "## 推导链路",
        "",
        "| step | source | target | confidence | evidence | note |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''))}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('evidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(["", "## 类型", "", "| index | full_name | methods | fields | token | note |", "| ---: | --- | ---: | ---: | --- | --- |"])
    for row in type_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('index', ''))} | "
            f"`{_markdown_table_cell(row.get('full_name', ''))}` | "
            f"{_markdown_table_cell(row.get('method_count', ''))} | "
            f"{_markdown_table_cell(row.get('field_count', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(
        [
            "",
            f"## 方法（{', '.join(f'{key}:{value}' for key, value in role_counts.most_common())}）",
            "",
            "| role | method | parameters | token | note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in method_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('role', ''))} | "
            f"`{_markdown_table_cell(row.get('qualified_name', ''))}` | "
            f"{_markdown_table_cell(row.get('parameters', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''))} |"
        )

    lines.extend(["", "## 字符串", "", "| source | index | term | value |", "| --- | ---: | --- | --- |"])
    for row in string_rows[:80]:
        lines.append(
            f"| {_markdown_table_cell(row.get('source_table', ''))} | "
            f"{_markdown_table_cell(row.get('index', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''))} | "
            f"`{_markdown_table_cell(row.get('value', ''))}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_unity_login_receiver_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = _ensure_il2cpp_metadata_index(root, export_base)
    if not (output_dir / "apk_dex_login_body_flow.tsv").is_file():
        build_fanxiu_apk_dex_login_body_probe(apk_root=root, export_root=export_base)

    type_rows, method_rows, string_rows = _collect_unity_login_receiver_rows(output_dir)
    flow_rows = _build_unity_login_receiver_flow(output_dir, method_rows, string_rows)
    role_counts = Counter(str(row.get("role", "")) for row in method_rows)

    _write_tsv(
        output_dir / "apk_unity_login_receiver_flow.tsv",
        ["step", "source", "target", "confidence", "evidence", "note"],
        flow_rows,
    )
    _write_tsv(
        output_dir / "apk_unity_login_receiver_types.tsv",
        ["index", "full_name", "method_start", "method_count", "field_start", "field_count", "token", "note"],
        type_rows,
    )
    _write_tsv(
        output_dir / "apk_unity_login_receiver_methods.tsv",
        ["role", "owner", "name", "qualified_name", "parameters", "return_type", "token", "note"],
        method_rows,
    )
    _write_tsv(
        output_dir / "apk_unity_login_receiver_strings.tsv",
        ["source_table", "index", "term", "value", "note"],
        string_rows,
    )
    _write_unity_login_receiver_markdown(
        output_dir / "apk_unity_login_receiver_report.md",
        root=root,
        output_dir=output_dir,
        flow_rows=flow_rows,
        type_rows=type_rows,
        method_rows=method_rows,
        string_rows=string_rows,
    )

    result: dict[str, Any] = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "flow_rows": len(flow_rows),
            "types": len(type_rows),
            "methods": len(method_rows),
            "strings": len(string_rows),
            "method_roles": dict(role_counts),
        },
        "outputs": {
            "summary": str(output_dir / "apk_unity_login_receiver_report.json"),
            "markdown": str(output_dir / "apk_unity_login_receiver_report.md"),
            "flow": str(output_dir / "apk_unity_login_receiver_flow.tsv"),
            "types": str(output_dir / "apk_unity_login_receiver_types.tsv"),
            "methods": str(output_dir / "apk_unity_login_receiver_methods.tsv"),
            "strings": str(output_dir / "apk_unity_login_receiver_strings.tsv"),
        },
    }
    (output_dir / "apk_unity_login_receiver_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_GAMELOGIN_BRIDGE_METHOD_TERMS = (
    "LuaBridge.Login.GameLoginBridge",
    "LuaBridge_Login_GameLoginBridgeWrap",
    "F_GetServerList",
    "F_ServerStatusCheck",
    "F_WebLoginCheck",
    "F_GetBulletinBoardInfo",
    "F_ReserveBoardInfo",
    "<F_GetServerList>",
    "<F_ServerStatusCheck>",
    "Core.Net.Http.HttpManager",
    "RequestWebJsonData",
    "requestOneUrl",
    "UnityEngine.Networking.UnityWebRequest.Get",
    "UnityEngine.Networking.UnityWebRequest.Post",
    "UnityWebRequestDelegate",
)

_GAMELOGIN_BRIDGE_STRING_TERMS = (
    "GameLoginBridge",
    "F_GetServerList",
    "F_ServerStatusCheck",
    "ServerListUrl",
    "ServerCheckUrl",
    "ImportServerUrl",
    "TestLoginCheckInUrl",
    "RequestWebJsonData",
    "requestOneUrl",
    "HttpManager",
    "UnityWebRequest",
    "jsonData",
    "callbackid",
    "gzip",
)

_GAMELOGIN_BRIDGE_BINARY_TERMS = (
    "GameLoginBridge",
    "F_GetServerList",
    "F_ServerStatusCheck",
    "ServerListUrl",
    "ServerCheckUrl",
    "RequestWebJsonData",
    "requestOneUrl",
    "HttpManager",
    "UnityWebRequest",
    "gzip",
)


def _ensure_il2cpp_metadata_index(root: Path, export_base: Path) -> Path:
    output_dir = (export_base / "apk_static_index").resolve()
    required = (output_dir / "il2cpp_methods.tsv", output_dir / "il2cpp_types.tsv", output_dir / "il2cpp_strings.tsv")
    if all(path.is_file() for path in required):
        return output_dir
    from backend.core.fanxiu_il2cpp_metadata import build_fanxiu_il2cpp_metadata_probe

    build_fanxiu_il2cpp_metadata_probe(apk_root=root, export_root=export_base)
    return output_dir


def _gamelogin_method_role(row: dict[str, str]) -> str:
    owner = row.get("owner", "")
    name = row.get("name", "")
    qualified = row.get("qualified_name", "")
    if owner == "LuaBridge.Login.GameLoginBridge":
        return "bridge_api"
    if owner == "LuaBridge_Login_GameLoginBridgeWrap":
        return "tolua_wrap"
    if "<F_GetServerList>" in qualified or "<F_ServerStatusCheck>" in qualified:
        return "async_callback"
    if owner == "Core.Net.Http.HttpManager":
        return "http_manager"
    if owner == "UnityWebRequestDelegate":
        return "unity_webrequest_delegate"
    if owner == "UnityEngine.Networking.UnityWebRequest" and name in {"Get", "Post", "SendWebRequest"}:
        return "unity_webrequest_api"
    return "related_method"


def _gamelogin_method_note(row: dict[str, str]) -> str:
    qualified = row.get("qualified_name", "")
    parameters = row.get("parameters", "")
    if qualified.endswith("GameLoginBridge.F_GetServerList"):
        return "Lua 调用的服务器列表 bridge 入口，参数为 callbackid/jsonData/iszip。"
    if qualified.endswith("GameLoginBridge.F_ServerStatusCheck"):
        return "进服前区服状态检查 bridge 入口，参数为 callbackid/jsonData。"
    if "<F_GetServerList>b__0" in qualified and "www:" in parameters:
        return "F_GetServerList 成功回调闭包，参数类型指向 Unity 下载结果对象。"
    if "<F_GetServerList>b__1" in qualified and "error:" in parameters:
        return "F_GetServerList 错误回调闭包，参数为错误字符串。"
    if "<F_ServerStatusCheck>b__0" in qualified and "www:" in parameters:
        return "F_ServerStatusCheck 成功回调闭包，参数类型指向 Unity 下载结果对象。"
    if "<F_ServerStatusCheck>b__1" in qualified and "error:" in parameters:
        return "F_ServerStatusCheck 错误回调闭包，参数为错误字符串。"
    if row.get("owner", "") == "Core.Net.Http.HttpManager":
        return "通用 HTTP 工具类候选；metadata 只能证明接口存在，不能证明调用边。"
    if row.get("owner", "") == "UnityEngine.Networking.UnityWebRequest":
        return "UnityWebRequest 通用 API；具体调用关系需要方法体反编译。"
    return "IL2CPP metadata 方法符号；只能证明签名和命名。"


def _collect_gamelogin_bridge_methods(output_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    terms = tuple(term.lower() for term in _GAMELOGIN_BRIDGE_METHOD_TERMS)
    for row in _read_tsv_rows(output_dir / "il2cpp_methods.tsv"):
        haystack = " | ".join(str(row.get(field, "")) for field in ("owner", "name", "qualified_name", "parameters"))
        lower = haystack.lower()
        if not any(term.lower() in lower for term in terms):
            continue
        role = _gamelogin_method_role(row)
        key = (role, row.get("owner", ""), row.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "role": role,
                "index": row.get("index", ""),
                "declaring_type": row.get("declaring_type", ""),
                "owner": row.get("owner", ""),
                "name": row.get("name", ""),
                "qualified_name": row.get("qualified_name", ""),
                "parameters": row.get("parameters", ""),
                "return_type": row.get("return_type_name", "") or row.get("return_type", ""),
                "parameter_start": row.get("parameter_start", ""),
                "parameter_count": row.get("parameter_count", ""),
                "token": row.get("token", ""),
                "note": _gamelogin_method_note(row),
            }
        )
    role_order = {
        "bridge_api": 0,
        "async_callback": 1,
        "tolua_wrap": 2,
        "http_manager": 3,
        "unity_webrequest_api": 4,
        "unity_webrequest_delegate": 5,
        "related_method": 6,
    }
    rows.sort(
        key=lambda row: (
            role_order.get(str(row.get("role", "")), 99),
            str(row.get("owner", "")),
            str(row.get("name", "")),
            int(str(row.get("index") or "0")),
        )
    )
    return rows


def _collect_gamelogin_bridge_types(output_dir: Path, method_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    terms = (
        "LuaBridge.Login.GameLoginBridge",
        "LuaBridge_Login_GameLoginBridgeWrap",
        "Core.Net.Http.HttpManager",
        "UnityWebRequestDelegate",
        "UnityEngine.Networking.UnityWebRequest",
    )
    method_type_indexes = {str(row.get("declaring_type", "")) for row in method_rows if row.get("declaring_type", "") != ""}
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _read_tsv_rows(output_dir / "il2cpp_types.tsv"):
        index = row.get("index", "")
        full_name = row.get("full_name", "")
        name = row.get("name", "")
        haystack = f"{full_name}|{name}"
        if index not in method_type_indexes and not any(term in haystack for term in terms):
            continue
        if index in seen:
            continue
        seen.add(index)
        rows.append(
            {
                "index": index,
                "name": name,
                "namespace": row.get("namespace", ""),
                "full_name": full_name,
                "method_start": row.get("method_start", ""),
                "method_count": row.get("method_count", ""),
                "field_start": row.get("field_start", ""),
                "field_count": row.get("field_count", ""),
                "token": row.get("token", ""),
                "note": "DisplayClass 为异步闭包承载类型；其字段和方法体需要反编译才能完整确认捕获变量。"
                if "DisplayClass" in full_name
                else "IL2CPP metadata 类型符号。",
            }
        )
    rows.sort(key=lambda row: (int(str(row.get("index") or "0")), str(row.get("full_name", ""))))
    return rows


def _collect_gamelogin_bridge_fields(output_dir: Path, type_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    field_rows = _read_tsv_rows(output_dir / "il2cpp_fields.tsv")
    selected: list[dict[str, object]] = []
    for type_row in type_rows:
        try:
            start = int(str(type_row.get("field_start") or "-1"))
            count = int(str(type_row.get("field_count") or "0"))
        except ValueError:
            continue
        if start < 0 or count <= 0:
            continue
        for field in field_rows[start : start + count]:
            selected.append(
                {
                    "type_index": type_row.get("index", ""),
                    "type_full_name": type_row.get("full_name", ""),
                    "field_index": field.get("index", ""),
                    "owner": field.get("owner", ""),
                    "name": field.get("name", ""),
                    "qualified_name": field.get("qualified_name", ""),
                    "field_type": field.get("type_name", "") or field.get("type_index", ""),
                    "token": field.get("token", ""),
                    "note": "GameLoginBridge 异步闭包捕获字段。"
                    if "DisplayClass" in str(type_row.get("full_name", ""))
                    else "相关类型字段；是否被当前 bridge 使用需要方法体确认。",
                }
            )
    selected.sort(
        key=lambda row: (
            int(str(row.get("type_index") or "0")),
            int(str(row.get("field_index") or "0")),
            str(row.get("name", "")),
        )
    )
    return selected


def _collect_gamelogin_bridge_strings(output_dir: Path, *, row_limit: int = 300) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_table, index_field in (("il2cpp_strings.tsv", "string_index"), ("il2cpp_string_literals.tsv", "index")):
        for row in _read_tsv_rows(output_dir / source_table):
            value = row.get("value", "")
            lower = value.lower()
            matched = [term for term in _GAMELOGIN_BRIDGE_STRING_TERMS if term.lower() in lower]
            if not matched:
                continue
            key = (source_table, row.get(index_field, ""), value)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source_table": source_table,
                    "index": row.get(index_field, ""),
                    "term": ",".join(matched[:5]),
                    "value": value,
                    "note": "metadata 字符串/字符串字面量；可证明命名或常量存在，不能单独证明调用关系。",
                }
            )
            if len(rows) >= row_limit:
                return rows
    rows.sort(key=lambda row: (str(row.get("source_table", "")), int(str(row.get("index") or "0"))))
    return rows


def _collect_libil2cpp_binary_string_hits(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    lib_root = root / "lib"
    if not lib_root.is_dir():
        return rows
    for path in sorted(lib_root.glob("*/libil2cpp.so"), key=lambda item: item.as_posix().lower()):
        data = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        abi = path.parent.name
        for term in _GAMELOGIN_BRIDGE_BINARY_TERMS:
            needle = term.encode("utf-8")
            offsets: list[int] = []
            start = 0
            total = 0
            while True:
                found = data.find(needle, start)
                if found < 0:
                    break
                total += 1
                if len(offsets) < 8:
                    offsets.append(found)
                start = found + 1
            rows.append(
                {
                    "abi": abi,
                    "relative_path": rel,
                    "term": term,
                    "count": total,
                    "first_offsets": ",".join(f"0x{offset:X}" for offset in offsets),
                    "note": "libil2cpp.so 明文命中；0 表示该符号名/常量主要只在 metadata 或被编译引用。",
                }
            )
    rows.sort(key=lambda row: (str(row.get("abi", "")), str(row.get("term", ""))))
    return rows


def _write_gamelogin_bridge_markdown(
    path: Path,
    *,
    apk_root: Path,
    output_dir: Path,
    method_rows: list[dict[str, object]],
    type_rows: list[dict[str, object]],
    field_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
    binary_rows: list[dict[str, object]],
) -> None:
    method_roles = Counter(str(row.get("role", "")) for row in method_rows)
    binary_nonzero = [row for row in binary_rows if int(row.get("count") or 0) > 0]
    bridge_methods = [
        row
        for row in method_rows
        if str(row.get("qualified_name", "")).startswith("LuaBridge.Login.GameLoginBridge.")
    ]
    binary_summary = (
        ", ".join(f"{row.get('abi')}:{row.get('term')}={row.get('count')}" for row in binary_nonzero[:12])
        or "未命中 GameLoginBridge/ServerListUrl 等业务字符串"
    )
    lines = [
        "# 凡修 GameLoginBridge 静态探针",
        "",
        f"- APK 解包目录：`{apk_root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告使用 IL2CPP metadata、已有登录链路导出和 `libil2cpp.so` 明文字符串扫描；不反汇编方法体，不访问线上服务器。",
        "",
        "## 结论",
        "",
        f"- GameLoginBridge 方法：{len(bridge_methods)} 条；关键角色分布：{', '.join(f'{k}:{v}' for k, v in method_roles.most_common()) or '无'}。",
        "- `F_GetServerList(callbackid:int, jsonData:string, iszip:bool)` 与 `F_ServerStatusCheck(callbackid:int, jsonData:string)` 在 metadata 中可见。",
        "- `F_GetServerList` 和 `F_ServerStatusCheck` 都有成功/失败异步闭包：成功分支参数名为 `www`，失败分支参数名为 `error`，说明 bridge 内部大概率走 Unity/HTTP 异步请求后回调 Lua。",
        f"- `libil2cpp.so` 明文命中：{binary_summary}。",
        "- 当前边界：metadata 没有 IL2CPP 方法体地址/控制流；若要确认 HTTP GET/POST、URL 拼接、gzip 处理和回调数据处理，需要把 `global-metadata.dat` 与 `libil2cpp.so` 用 IL2CPP 反编译/反汇编工具对齐。",
        "",
        "## Bridge 与回调方法",
        "",
        "| role | owner | method | parameters | return | token | note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in method_rows:
        if row.get("role") not in {"bridge_api", "async_callback", "tolua_wrap"}:
            continue
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('role', ''))} | "
            f"{_markdown_table_cell(row.get('owner', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('parameters', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('return_type', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## HTTP/Unity 候选方法", "", "| role | owner | method | parameters | note |", "| --- | --- | --- | --- | --- |"])
    for row in method_rows:
        if row.get("role") in {"bridge_api", "async_callback", "tolua_wrap"}:
            continue
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('role', ''))} | "
            f"{_markdown_table_cell(row.get('owner', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('parameters', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## 相关类型", "", "| index | full_name | methods | fields | token | note |", "| ---: | --- | ---: | ---: | --- | --- |"])
    for row in type_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('index', ''))} | "
            f"{_markdown_table_cell(row.get('full_name', ''))} | "
            f"{_markdown_table_cell(row.get('method_count', ''))} | "
            f"{_markdown_table_cell(row.get('field_count', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## 相关字段", "", "| type | field | field_type | token | note |", "| --- | --- | --- | --- | --- |"])
    for row in field_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('type_full_name', ''), limit=160)} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('field_type', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## metadata 字符串", "", "| 来源 | index | term | value |", "| --- | ---: | --- | --- |"])
    for row in string_rows[:80]:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('source_table', ''))} | "
            f"{_markdown_table_cell(row.get('index', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''), limit=120)} | "
            f"{_markdown_table_cell(row.get('value', ''), limit=260)} |"
        )

    lines.extend(["", "## libil2cpp.so 明文扫描", "", "| abi | term | count | first_offsets |", "| --- | --- | ---: | --- |"])
    for row in binary_rows:
        lines.append(
            "| "
            f"{_markdown_table_cell(row.get('abi', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''))} | "
            f"{_markdown_table_cell(row.get('count', ''))} | "
            f"{_markdown_table_cell(row.get('first_offsets', ''), limit=180)} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_apk_gamelogin_bridge_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = _ensure_il2cpp_metadata_index(root, export_base)
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    method_rows = _collect_gamelogin_bridge_methods(output_dir)
    type_rows = _collect_gamelogin_bridge_types(output_dir, method_rows)
    field_rows = _collect_gamelogin_bridge_fields(output_dir, type_rows)
    string_rows = _collect_gamelogin_bridge_strings(output_dir)
    binary_rows = _collect_libil2cpp_binary_string_hits(root)

    method_count = _write_tsv(
        output_dir / "apk_gamelogin_bridge_methods.tsv",
        [
            "role",
            "index",
            "declaring_type",
            "owner",
            "name",
            "qualified_name",
            "parameters",
            "return_type",
            "parameter_start",
            "parameter_count",
            "token",
            "note",
        ],
        method_rows,
    )
    type_count = _write_tsv(
        output_dir / "apk_gamelogin_bridge_types.tsv",
        ["index", "name", "namespace", "full_name", "method_start", "method_count", "field_start", "field_count", "token", "note"],
        type_rows,
    )
    field_count = _write_tsv(
        output_dir / "apk_gamelogin_bridge_fields.tsv",
        ["type_index", "type_full_name", "field_index", "owner", "name", "qualified_name", "field_type", "token", "note"],
        field_rows,
    )
    string_count = _write_tsv(
        output_dir / "apk_gamelogin_bridge_strings.tsv",
        ["source_table", "index", "term", "value", "note"],
        string_rows,
    )
    binary_count = _write_tsv(
        output_dir / "apk_gamelogin_bridge_binary_strings.tsv",
        ["abi", "relative_path", "term", "count", "first_offsets", "note"],
        binary_rows,
    )
    _write_gamelogin_bridge_markdown(
        output_dir / "apk_gamelogin_bridge_report.md",
        apk_root=root,
        output_dir=output_dir,
        method_rows=method_rows,
        type_rows=type_rows,
        field_rows=field_rows,
        string_rows=string_rows,
        binary_rows=binary_rows,
    )

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "methods": method_count,
            "types": type_count,
            "fields": field_count,
            "strings": string_count,
            "binary_terms": binary_count,
            "method_roles": dict(Counter(str(row.get("role", "")) for row in method_rows).most_common()),
            "binary_nonzero_terms": sum(1 for row in binary_rows if int(row.get("count") or 0) > 0),
        },
        "outputs": {
            "summary": str(output_dir / "apk_gamelogin_bridge_report.json"),
            "markdown": str(output_dir / "apk_gamelogin_bridge_report.md"),
            "methods": str(output_dir / "apk_gamelogin_bridge_methods.tsv"),
            "types": str(output_dir / "apk_gamelogin_bridge_types.tsv"),
            "fields": str(output_dir / "apk_gamelogin_bridge_fields.tsv"),
            "strings": str(output_dir / "apk_gamelogin_bridge_strings.tsv"),
            "binary_strings": str(output_dir / "apk_gamelogin_bridge_binary_strings.tsv"),
        },
    }
    (output_dir / "apk_gamelogin_bridge_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_PHONEHELPER_LOGIN_METHOD_TERMS = (
    "LuaBridge.PhoneHelp.PhoneHelperBridge",
    "LuaBridge_PhoneHelp_PhoneHelperBridgeWrap",
    "MU.Tool.PhoneHelper",
    "PhoneReceiver.PhoneMsgReceiver",
    "GameEnter",
    "F_SDKLogin",
    "SDKLogin",
    "GetLoginToken",
    "GetGameId",
    "GetPid",
    "F_GetPackageName",
    "F_GetPhoneVersion",
    "OnReceiveLogin",
    "OnReceiveCancelLogin",
)

_PHONEHELPER_LOGIN_STRING_TERMS = (
    "PhoneHelper",
    "PhoneHelperBridge",
    "F_SDKLogin",
    "SDKLogin",
    "GetLoginToken",
    "GetGameId",
    "GetPid",
    "F_GetPackageName",
    "F_GetPhoneVersion",
    "GameEnter",
    "OnReceiveLogin",
    "OnReceiveCancelLogin",
)

_PHONEHELPER_LOGIN_LUA_TERMS = (
    "PhoneHelper.SDKLogin",
    "PhoneHelper.GetLoginToken",
    "PhoneHelper.GetPid",
    "PhoneHelper.GetGameId",
    "PhoneHelper.F_GetPackageName",
    "PhoneHelper.F_GetPhoneVersion",
    "GetSDKServerList",
    "LoginToken",
    "GameLoginBridge.F_GetServerList",
    "LuaBridge.PhoneHelp.PhoneHelperBridge",
    "PhoneHelperBridge",
    "OnReceiveLogin",
)


def _phonehelper_login_method_role(row: dict[str, str]) -> str:
    owner = row.get("owner", "")
    name = row.get("name", "")
    if owner == "LuaBridge.PhoneHelp.PhoneHelperBridge":
        return "phonehelper_bridge_api"
    if owner == "LuaBridge_PhoneHelp_PhoneHelperBridgeWrap":
        return "phonehelper_tolua_wrap"
    if owner == "MU.Tool.PhoneHelper" and name == "F_SDKLogin":
        return "phonehelper_sdk_login"
    if owner == "MU.Tool.PhoneHelper":
        return "phonehelper_tool"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name == "OnReceiveLogin":
        return "unity_login_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name == "OnReceiveCancelLogin":
        return "unity_cancel_login_receiver"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name.startswith("OnReceive"):
        return "unity_phone_receiver"
    if owner == "GameEnter":
        return "game_enter_lifecycle"
    return "related_method"


def _phonehelper_login_method_note(row: dict[str, str]) -> str:
    owner = row.get("owner", "")
    name = row.get("name", "")
    if owner == "LuaBridge.PhoneHelp.PhoneHelperBridge" and name == "F_SDKLogin":
        return "Lua 可调用的 SDK 登录 bridge 入口。"
    if owner == "LuaBridge.PhoneHelp.PhoneHelperBridge" and name in {"GetLoginToken", "GetGameId", "GetPid"}:
        return "Lua 登录服请求前使用的渠道身份/登录态 getter。"
    if owner == "LuaBridge.PhoneHelp.PhoneHelperBridge" and name in {"F_GetPackageName", "F_GetPhoneVersion"}:
        return "服务器列表请求中使用的包名/版本 getter。"
    if owner == "MU.Tool.PhoneHelper" and name == "F_SDKLogin":
        return "PhoneHelper 工具层 SDK 登录入口；继续下钻需要 IL2CPP 方法体。"
    if owner == "PhoneReceiver.PhoneMsgReceiver" and name.startswith("OnReceive"):
        return "Java UnitySendMessage 回灌后的 Unity 接收方法候选。"
    if owner == "GameEnter":
        return "UnitySendMessage 的 GameObject/生命周期类型候选。"
    return "IL2CPP metadata 方法符号；只能证明签名和命名。"


def _collect_phonehelper_login_methods(output_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    terms = tuple(term.lower() for term in _PHONEHELPER_LOGIN_METHOD_TERMS)
    for row in _read_tsv_rows(output_dir / "il2cpp_methods.tsv"):
        haystack = " | ".join(str(row.get(field, "")) for field in ("owner", "name", "qualified_name", "parameters"))
        if not any(term in haystack.lower() for term in terms):
            continue
        key = (row.get("owner", ""), row.get("name", ""), row.get("parameters", ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "role": _phonehelper_login_method_role(row),
                "index": row.get("index", ""),
                "declaring_type": row.get("declaring_type", ""),
                "owner": row.get("owner", ""),
                "name": row.get("name", ""),
                "qualified_name": row.get("qualified_name", ""),
                "parameters": row.get("parameters", ""),
                "return_type": row.get("return_type_name", "") or row.get("return_type", ""),
                "token": row.get("token", ""),
                "note": _phonehelper_login_method_note(row),
            }
        )
    role_order = {
        "phonehelper_bridge_api": 0,
        "phonehelper_sdk_login": 1,
        "phonehelper_tool": 2,
        "phonehelper_tolua_wrap": 3,
        "unity_login_receiver": 4,
        "unity_cancel_login_receiver": 5,
        "unity_phone_receiver": 6,
        "game_enter_lifecycle": 7,
        "related_method": 8,
    }
    rows.sort(
        key=lambda row: (
            role_order.get(str(row.get("role", "")), 99),
            str(row.get("owner", "")),
            str(row.get("name", "")),
            int(str(row.get("index") or "0")),
        )
    )
    return rows


def _collect_phonehelper_login_strings(output_dir: Path, *, row_limit: int = 200) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_table, index_field in (("il2cpp_strings.tsv", "string_index"), ("il2cpp_string_literals.tsv", "index")):
        for row in _read_tsv_rows(output_dir / source_table):
            value = row.get("value", "")
            lower = value.lower()
            matched = [term for term in _PHONEHELPER_LOGIN_STRING_TERMS if term.lower() in lower]
            if not matched:
                continue
            key = (source_table, row.get(index_field, ""), value)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source_table": source_table,
                    "index": row.get(index_field, ""),
                    "term": ",".join(matched[:5]),
                    "value": value,
                    "note": "metadata 字符串/字符串字面量；用于辅助定位登录桥接命名。",
                }
            )
            if len(rows) >= row_limit:
                return rows
    rows.sort(key=lambda row: (str(row.get("source_table", "")), int(str(row.get("index") or "0"))))
    return rows


def _phonehelper_lua_category(line: str) -> str:
    if "PhoneHelper.SDKLogin" in line:
        return "lua_login_trigger"
    if any(term in line for term in ("PhoneHelper.GetLoginToken", "PhoneHelper.GetPid", "PhoneHelper.GetGameId", "PhoneHelper.F_GetPackageName", "PhoneHelper.F_GetPhoneVersion")):
        return "lua_identity_getter"
    if any(term in line for term in ("GetSDKServerList", "LoginToken", "GameLoginBridge.F_GetServerList")):
        return "lua_server_list_request"
    if "PhoneHelperBridge" in line:
        return "lua_bridge_wrapper"
    if "OnReceiveLogin" in line:
        return "lua_login_receiver"
    return "lua_related"


def _phonehelper_lua_note(category: str) -> str:
    return {
        "lua_login_trigger": "Lua UI/登录流程主动触发渠道 SDK 登录。",
        "lua_identity_getter": "Lua 登录服请求前读取渠道 pid/gid/token 或包体信息。",
        "lua_server_list_request": "Lua 登录服/区服列表请求上下文。",
        "lua_bridge_wrapper": "Lua 对 PhoneHelperBridge 的包装层。",
        "lua_login_receiver": "Lua 层同名登录接收线索。",
        "lua_related": "登录链路相关 Lua 线索。",
    }.get(category, "登录链路相关 Lua 线索。")


def _collect_phonehelper_login_lua_refs(export_base: Path, *, row_limit: int = 300) -> list[dict[str, object]]:
    lua_root = export_base / "by_source" / "lscripts"
    if not lua_root.is_dir():
        return []
    rows: list[dict[str, object]] = []
    terms_lower = tuple(term.lower() for term in _PHONEHELPER_LOGIN_LUA_TERMS)
    for path in sorted(lua_root.rglob("*.lua"), key=lambda item: item.as_posix().lower()):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            lower = line.lower()
            matched = [term for term, term_lower in zip(_PHONEHELPER_LOGIN_LUA_TERMS, terms_lower, strict=True) if term_lower in lower]
            if not matched:
                continue
            category = _phonehelper_lua_category(line)
            rows.append(
                {
                    "category": category,
                    "relative_path": path.relative_to(export_base).as_posix(),
                    "line": line_number,
                    "term": ",".join(matched[:5]),
                    "text": line.strip(),
                    "note": _phonehelper_lua_note(category),
                }
            )
            if len(rows) >= row_limit:
                return rows
    rows.sort(key=lambda row: (str(row.get("category", "")), str(row.get("relative_path", "")), int(str(row.get("line") or "0"))))
    return rows


def _build_phonehelper_login_flow(
    output_dir: Path,
    *,
    method_rows: list[dict[str, object]],
    lua_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    dex_flow = _read_tsv_rows(output_dir / "apk_dex_login_body_flow.tsv")
    unity_flow = _read_tsv_rows(output_dir / "apk_unity_login_receiver_flow.tsv")
    gamelogin_methods = _read_tsv_rows(output_dir / "apk_gamelogin_bridge_methods.tsv")
    method_names = {str(row.get("qualified_name", "")) for row in method_rows}
    lua_categories = {str(row.get("category", "")) for row in lua_rows}

    def flow_has(rows: list[dict[str, str]], needle: str) -> bool:
        lower = needle.lower()
        return any(lower in f"{row.get('source', '')} {row.get('target', '')} {row.get('note', '')}".lower() for row in rows)

    has_bridge_login = "LuaBridge.PhoneHelp.PhoneHelperBridge.F_SDKLogin" in method_names
    has_tool_login = "MU.Tool.PhoneHelper.F_SDKLogin" in method_names
    has_receiver = "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin" in method_names or flow_has(unity_flow, "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin")
    has_gamelogin_bridge = any(row.get("qualified_name") == "LuaBridge.Login.GameLoginBridge.F_GetServerList" for row in gamelogin_methods)
    has_identity_getters = "lua_identity_getter" in lua_categories
    has_server_request = "lua_server_list_request" in lua_categories

    return [
        {
            "step": "01-lua-login-trigger",
            "source": "Lua login UI / LoginAccount",
            "target": "PhoneHelper.SDKLogin()",
            "confidence": 95 if "lua_login_trigger" in lua_categories else 50,
            "evidence": "apk_phonehelper_login_lua_refs.tsv",
            "note": "Lua 登录界面先触发渠道 SDK 登录，而不是直接拿服务器列表。",
        },
        {
            "step": "02-phonehelper-bridge",
            "source": "PhoneHelper.SDKLogin()",
            "target": "LuaBridge.PhoneHelp.PhoneHelperBridge.F_SDKLogin / MU.Tool.PhoneHelper.F_SDKLogin",
            "confidence": 90 if has_bridge_login and has_tool_login else 70 if has_bridge_login or has_tool_login else 40,
            "evidence": "il2cpp_methods.tsv / apk_phonehelper_login_methods.tsv",
            "note": "metadata 可见 LuaBridge 与工具层同名 SDK 登录入口；具体内部调用边仍需方法体。",
        },
        {
            "step": "03-channel-sdk-entry",
            "source": "MU.Tool.PhoneHelper.F_SDKLogin",
            "target": "FlameUnityActivity.SDKLogin -> SQwanCore.login",
            "confidence": 80 if flow_has(dex_flow, "SQwanCore.login") else 55,
            "evidence": "apk_dex_login_body_flow.tsv",
            "note": "DEX 方法体轻量探针已确认 Java SDK 外壳登录链；PhoneHelper 到 Java 的跨边界调用需要 IL2CPP 方法体补证。",
        },
        {
            "step": "04-java-success-callback",
            "source": "FlameUnityActivity$7.onSuccess(token/gid/pid)",
            "target": "SDKCallback.OnSDKLoginData -> UnitySendMessage(GameEnter, OnReceiveLogin, data)",
            "confidence": 95 if flow_has(dex_flow, "UnitySendMessage") and flow_has(dex_flow, "OnReceiveLogin") else 70,
            "evidence": "apk_dex_login_body_flow.tsv",
            "note": "Java 成功回调把 token/gid/pid 拼成 Unity 登录消息数据。",
        },
        {
            "step": "05-unity-login-receiver",
            "source": "UnitySendMessage(GameEnter, OnReceiveLogin, data)",
            "target": "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin(data:string)",
            "confidence": 90 if has_receiver else 60,
            "evidence": "apk_unity_login_receiver_flow.tsv / il2cpp_methods.tsv",
            "note": "GameEnter 是消息目标名候选，PhoneMsgReceiver 是同名方法接收组件候选。",
        },
        {
            "step": "06-server-list-after-sdk-login",
            "source": "Unity/Lua login model",
            "target": "GetSDKServerList(Pid, LoginToken) -> GameLoginBridge.F_GetServerList(jsonData)",
            "confidence": 85 if has_server_request and has_gamelogin_bridge else 65 if has_server_request else 45,
            "evidence": "apk_phonehelper_login_lua_refs.tsv / apk_gamelogin_bridge_methods.tsv",
            "note": "Lua 服务器列表请求使用 Pid/LoginToken；OnReceiveLogin 如何触发该 Lua 流程仍需 IL2CPP 方法体确认。",
        },
        {
            "step": "07-request-identity-fields",
            "source": "GetServerInfo.GetSDKServerList",
            "target": "pid/token/cid/gid/bundleId/bundleVersion/gzip",
            "confidence": 90 if has_identity_getters and has_server_request else 60,
            "evidence": "apk_phonehelper_login_lua_refs.tsv",
            "note": "Lua 可见 pid/gid、包名、版本和 gzip 字段参与登录服/区服列表请求。",
        },
        {
            "step": "08-current-boundary",
            "source": "PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin",
            "target": "exact token split and Lua login model call",
            "confidence": 50,
            "evidence": "metadata lacks IL2CPP method body",
            "note": "下一层真正需要 IL2CPP 反编译工具，才能确认 data 字符串拆分与 Lua 调用细节。",
        },
    ]


def _write_phonehelper_login_markdown(
    path: Path,
    *,
    apk_root: Path,
    output_dir: Path,
    method_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
    lua_rows: list[dict[str, object]],
    flow_rows: list[dict[str, object]],
) -> None:
    method_roles = Counter(str(row.get("role", "")) for row in method_rows)
    lua_categories = Counter(str(row.get("category", "")) for row in lua_rows)
    lines = [
        "# 凡修 PhoneHelper 登录上下文探针",
        "",
        f"- APK 解包目录：`{apk_root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告把 Lua、IL2CPP metadata、DEX 轻量方法体探针串到同一张登录上下文图里；不注入、不改包、不访问线上接口。",
        "",
        "## 结论",
        "",
        "- 当前可静态串起：`PhoneHelper.SDKLogin() -> PhoneHelperBridge.F_SDKLogin / MU.Tool.PhoneHelper.F_SDKLogin -> Java SDKLogin/SQwanCore.login -> UnitySendMessage(GameEnter, OnReceiveLogin, data) -> PhoneMsgReceiver.OnReceiveLogin(data)`。",
        "- Java 成功回调侧已能看到 `token/gid/pid` 回灌；Lua 服务器列表侧已能看到 `GetSDKServerList(Pid, LoginToken)` 与 `pid/token/cid/gid/bundleId/bundleVersion/gzip` 请求字段。",
        "- 关键缺口很明确：`PhoneMsgReceiver.OnReceiveLogin` 的 IL2CPP 方法体不可读，所以还不能严谨证明它如何拆 `1__token__gid__pid`、如何触发 Lua 登录服流程。",
        "- 因此这一步已经接近普通静态索引边界；继续深入该链路，需要 IL2CPP 反编译/反汇编工具，而不是继续 grep。",
        "",
        "## 推导链路",
        "",
        "| step | source | target | confidence | evidence | note |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in flow_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('step', ''))} | "
            f"`{_markdown_table_cell(row.get('source', ''))}` | "
            f"`{_markdown_table_cell(row.get('target', ''), limit=220)}` | "
            f"{_markdown_table_cell(row.get('confidence', ''))} | "
            f"{_markdown_table_cell(row.get('evidence', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=260)} |"
        )

    lines.extend(
        [
            "",
            f"## IL2CPP 方法（{', '.join(f'{key}:{value}' for key, value in method_roles.most_common()) or '无'}）",
            "",
            "| role | owner | method | parameters | return | token | note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in method_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('role', ''))} | "
            f"{_markdown_table_cell(row.get('owner', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('parameters', ''), limit=220)} | "
            f"{_markdown_table_cell(row.get('return_type', ''))} | "
            f"{_markdown_table_cell(row.get('token', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(
        [
            "",
            f"## Lua 线索（{', '.join(f'{key}:{value}' for key, value in lua_categories.most_common()) or '无'}）",
            "",
            "| category | file | line | term | text | note |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in lua_rows[:160]:
        lines.append(
            f"| {_markdown_table_cell(row.get('category', ''))} | "
            f"{_markdown_table_cell(row.get('relative_path', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('line', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''), limit=120)} | "
            f"`{_markdown_table_cell(row.get('text', ''), limit=260)}` | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## metadata 字符串", "", "| source | index | term | value |", "| --- | ---: | --- | --- |"])
    for row in string_rows[:80]:
        lines.append(
            f"| {_markdown_table_cell(row.get('source_table', ''))} | "
            f"{_markdown_table_cell(row.get('index', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''), limit=120)} | "
            f"`{_markdown_table_cell(row.get('value', ''), limit=260)}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_phonehelper_login_context_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = _ensure_il2cpp_metadata_index(root, export_base)
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not (output_dir / "apk_dex_login_body_flow.tsv").is_file():
        build_fanxiu_apk_dex_login_body_probe(apk_root=root, export_root=export_base)
    if not (output_dir / "apk_unity_login_receiver_flow.tsv").is_file():
        build_fanxiu_apk_unity_login_receiver_probe(apk_root=root, export_root=export_base)
    if not (output_dir / "apk_gamelogin_bridge_methods.tsv").is_file():
        build_fanxiu_apk_gamelogin_bridge_probe(apk_root=root, export_root=export_base)

    method_rows = _collect_phonehelper_login_methods(output_dir)
    string_rows = _collect_phonehelper_login_strings(output_dir)
    lua_rows = _collect_phonehelper_login_lua_refs(export_base)
    flow_rows = _build_phonehelper_login_flow(output_dir, method_rows=method_rows, lua_rows=lua_rows)

    method_count = _write_tsv(
        output_dir / "apk_phonehelper_login_context_methods.tsv",
        ["role", "index", "declaring_type", "owner", "name", "qualified_name", "parameters", "return_type", "token", "note"],
        method_rows,
    )
    string_count = _write_tsv(
        output_dir / "apk_phonehelper_login_context_strings.tsv",
        ["source_table", "index", "term", "value", "note"],
        string_rows,
    )
    lua_count = _write_tsv(
        output_dir / "apk_phonehelper_login_context_lua_refs.tsv",
        ["category", "relative_path", "line", "term", "text", "note"],
        lua_rows,
    )
    flow_count = _write_tsv(
        output_dir / "apk_phonehelper_login_context_flow.tsv",
        ["step", "source", "target", "confidence", "evidence", "note"],
        flow_rows,
    )
    _write_phonehelper_login_markdown(
        output_dir / "apk_phonehelper_login_context_report.md",
        apk_root=root,
        output_dir=output_dir,
        method_rows=method_rows,
        string_rows=string_rows,
        lua_rows=lua_rows,
        flow_rows=flow_rows,
    )

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "methods": method_count,
            "strings": string_count,
            "lua_refs": lua_count,
            "flows": flow_count,
            "method_roles": dict(Counter(str(row.get("role", "")) for row in method_rows).most_common()),
            "lua_categories": dict(Counter(str(row.get("category", "")) for row in lua_rows).most_common()),
        },
        "outputs": {
            "summary": str(output_dir / "apk_phonehelper_login_context_report.json"),
            "markdown": str(output_dir / "apk_phonehelper_login_context_report.md"),
            "methods": str(output_dir / "apk_phonehelper_login_context_methods.tsv"),
            "strings": str(output_dir / "apk_phonehelper_login_context_strings.tsv"),
            "lua_refs": str(output_dir / "apk_phonehelper_login_context_lua_refs.tsv"),
            "flow": str(output_dir / "apk_phonehelper_login_context_flow.tsv"),
        },
    }
    (output_dir / "apk_phonehelper_login_context_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_IL2CPP_BOUNDARY_STRING_TERMS = (
    "PhoneMsgReceiver",
    "OnReceiveLogin",
    "OnReceiveCancelLogin",
    "F_SDKLogin",
    "PhoneHelperBridge",
    "LuaBridge.PhoneHelp.PhoneHelperBridge",
    "MU.Tool.PhoneHelper",
    "GameEnter",
    "UnitySendMessage",
    "GameLoginBridge",
    "F_GetServerList",
    "F_ServerStatusCheck",
    "RequestWebJsonData",
    "UnityWebRequest",
)


def _c_string_from_buffer(buf: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    end = buf.find(b"\x00", offset)
    if end < 0:
        end = len(buf)
    return buf[offset:end].decode("utf-8", errors="replace")


def _parse_libil2cpp_elf(path: Path) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    data = path.read_bytes()
    summary: dict[str, object] = {
        "relative_path": path.name,
        "size": len(data),
        "is_elf": 0,
        "elf_class": "",
        "machine": "",
        "entry": "",
        "section_count": 0,
        "program_header_count": 0,
        "has_symtab": 0,
        "dynsym_total": 0,
        "dynsym_named": 0,
        "dynsym_funcs": 0,
        "dynsym_il2cpp_exports": 0,
        "needed": "",
        "note": "",
    }
    if len(data) < 64 or data[:4] != b"\x7fELF":
        summary["note"] = "不是 ELF 文件或文件不完整。"
        return summary, [], []
    if data[4] != 2:
        summary["is_elf"] = 1
        summary["elf_class"] = str(data[4])
        summary["note"] = "当前探针只解析 64-bit ELF。"
        return summary, [], []
    endian = "<" if data[5] == 1 else ">"
    elf_header = struct.Struct(endian + "16sHHIQQQIHHHHHH")
    (
        _e_ident,
        _e_type,
        e_machine,
        _e_version,
        e_entry,
        _e_phoff,
        e_shoff,
        _e_flags,
        _e_ehsize,
        _e_phentsize,
        e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = elf_header.unpack_from(data, 0)
    section_header = struct.Struct(endian + "IIQQQQIIQQ")
    sections: list[dict[str, object]] = []
    for index in range(e_shnum):
        offset = e_shoff + index * e_shentsize
        if offset + section_header.size > len(data):
            break
        values = section_header.unpack_from(data, offset)
        sections.append(
            {
                "index": index,
                "name_offset": values[0],
                "type": values[1],
                "flags": values[2],
                "addr": values[3],
                "offset": values[4],
                "size": values[5],
                "link": values[6],
                "info": values[7],
                "addralign": values[8],
                "entsize": values[9],
                "name": "",
            }
        )
    shstr = b""
    if 0 <= e_shstrndx < len(sections):
        shstr_section = sections[e_shstrndx]
        start = int(shstr_section["offset"])
        size = int(shstr_section["size"])
        shstr = data[start : start + size]
    for section in sections:
        section["name"] = _c_string_from_buffer(shstr, int(section["name_offset"]))

    section_rows: list[dict[str, object]] = []
    for section in sections:
        name = str(section.get("name", ""))
        if name in {".dynsym", ".symtab", ".dynstr", ".strtab", ".text", ".rodata", ".dynamic", ".rela.dyn", ".rela.plt", ".plt", ".got", ".init_array"} or "il2cpp" in name.lower():
            section_rows.append(
                {
                    "relative_path": path.name,
                    "index": section.get("index", ""),
                    "name": name,
                    "type": section.get("type", ""),
                    "addr": f"0x{int(section.get('addr') or 0):X}",
                    "offset": f"0x{int(section.get('offset') or 0):X}",
                    "size": f"0x{int(section.get('size') or 0):X}",
                    "entsize": section.get("entsize", ""),
                    "link": section.get("link", ""),
                    "note": "业务方法体主要在此类代码/只读区；需要 metadata registration 对齐才能定位具体 C# 方法。"
                    if name in {".text", "il2cpp", ".rodata"}
                    else "ELF 解析辅助节。",
                }
            )

    symbol_rows: list[dict[str, object]] = []
    dynsym_total = dynsym_named = dynsym_funcs = dynsym_il2cpp_exports = 0
    sym_struct = struct.Struct(endian + "IBBHQQ")
    for symbol_section_name in (".dynsym", ".symtab"):
        symbol_section = next((section for section in sections if section.get("name") == symbol_section_name), None)
        if symbol_section_name == ".symtab" and symbol_section:
            summary["has_symtab"] = 1
        if not symbol_section or not int(symbol_section.get("entsize") or 0):
            continue
        link = int(symbol_section.get("link") or -1)
        string_section = sections[link] if 0 <= link < len(sections) else None
        string_buffer = b""
        if string_section:
            start = int(string_section["offset"])
            size = int(string_section["size"])
            string_buffer = data[start : start + size]
        entry_count = int(symbol_section["size"]) // int(symbol_section["entsize"])
        for index in range(entry_count):
            offset = int(symbol_section["offset"]) + index * int(symbol_section["entsize"])
            if offset + sym_struct.size > len(data):
                break
            st_name, st_info, _st_other, st_shndx, st_value, st_size = sym_struct.unpack_from(data, offset)
            symbol_type = st_info & 0x0F
            symbol_bind = st_info >> 4
            name = _c_string_from_buffer(string_buffer, st_name)
            if symbol_section_name == ".dynsym":
                dynsym_total += 1
                if name:
                    dynsym_named += 1
                if symbol_type == 2:
                    dynsym_funcs += 1
            if not name:
                continue
            is_il2cpp_runtime = name.startswith("il2cpp_") or "il2cpp" in name.lower()
            is_business = any(term.lower() in name.lower() for term in _IL2CPP_BOUNDARY_STRING_TERMS)
            if symbol_section_name == ".dynsym" and is_il2cpp_runtime:
                dynsym_il2cpp_exports += 1
            if not is_il2cpp_runtime and not is_business:
                continue
            symbol_rows.append(
                {
                    "relative_path": path.name,
                    "source": symbol_section_name,
                    "index": index,
                    "name": name,
                    "type": symbol_type,
                    "bind": symbol_bind,
                    "section_index": st_shndx,
                    "value": f"0x{st_value:X}",
                    "size": st_size,
                    "category": "business_symbol" if is_business else "il2cpp_runtime_api",
                    "note": "若只有 IL2CPP runtime API 而没有业务方法名，说明 libil2cpp 已剥离 C# 业务符号。",
                }
            )

    needed: list[str] = []
    dynamic_section = next((section for section in sections if section.get("name") == ".dynamic"), None)
    dynstr_section = next((section for section in sections if section.get("name") == ".dynstr"), None)
    if dynamic_section and dynstr_section:
        dyn_buffer = data[int(dynstr_section["offset"]) : int(dynstr_section["offset"]) + int(dynstr_section["size"])]
        dyn_struct = struct.Struct(endian + "qQ")
        entsize = int(dynamic_section.get("entsize") or dyn_struct.size)
        for offset in range(int(dynamic_section["offset"]), int(dynamic_section["offset"]) + int(dynamic_section["size"]), entsize):
            if offset + dyn_struct.size > len(data):
                break
            tag, value = dyn_struct.unpack_from(data, offset)
            if tag == 0:
                break
            if tag == 1:
                needed.append(_c_string_from_buffer(dyn_buffer, int(value)))

    summary.update(
        {
            "is_elf": 1,
            "elf_class": "ELF64",
            "machine": e_machine,
            "entry": f"0x{e_entry:X}",
            "section_count": len(sections),
            "program_header_count": e_phnum,
            "dynsym_total": dynsym_total,
            "dynsym_named": dynsym_named,
            "dynsym_funcs": dynsym_funcs,
            "dynsym_il2cpp_exports": dynsym_il2cpp_exports,
            "needed": ",".join(needed),
            "note": "存在动态符号表但缺少 C# 业务符号；普通静态 grep 无法定位 IL2CPP 业务方法体。",
        }
    )
    symbol_rows.sort(key=lambda row: (str(row.get("category", "")), str(row.get("name", ""))))
    return summary, section_rows, symbol_rows


def _collect_il2cpp_boundary_string_hits(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    lib_root = root / "lib"
    if not lib_root.is_dir():
        return rows
    for path in sorted(lib_root.glob("*/libil2cpp.so"), key=lambda item: item.as_posix().lower()):
        data = path.read_bytes()
        abi = path.parent.name
        relative_path = path.relative_to(root).as_posix()
        for term in _IL2CPP_BOUNDARY_STRING_TERMS:
            needle = term.encode("utf-8")
            offsets: list[int] = []
            start = 0
            total = 0
            while True:
                found = data.find(needle, start)
                if found < 0:
                    break
                total += 1
                if len(offsets) < 8:
                    offsets.append(found)
                start = found + 1
            rows.append(
                {
                    "abi": abi,
                    "relative_path": relative_path,
                    "term": term,
                    "count": total,
                    "first_offsets": ",".join(f"0x{offset:X}" for offset in offsets),
                    "note": "0 表示该业务名不以明文形式裸露在 libil2cpp.so 中；通常只存在于 global-metadata.dat。",
                }
            )
    return rows


def _write_il2cpp_binary_boundary_markdown(
    path: Path,
    *,
    apk_root: Path,
    output_dir: Path,
    summary_rows: list[dict[str, object]],
    section_rows: list[dict[str, object]],
    symbol_rows: list[dict[str, object]],
    string_rows: list[dict[str, object]],
) -> None:
    business_symbols = [row for row in symbol_rows if row.get("category") == "business_symbol"]
    business_string_hits = [row for row in string_rows if int(row.get("count") or 0) > 0 and row.get("term") != "UnityWebRequest"]
    lines = [
        "# 凡修 libil2cpp 二进制边界探针",
        "",
        f"- APK 解包目录：`{apk_root}`",
        f"- 索引目录：`{output_dir}`",
        "- 说明：本报告只解析 ELF 头、节表、动态符号表和明文字符串；不反汇编、不还原控制流、不访问线上接口。",
        "",
        "## 结论",
        "",
        "- `libil2cpp.so` 是 ARM64 ELF 共享库，包含 `.dynsym`，但当前未发现 `.symtab`。",
        f"- 动态符号中业务符号命中 {len(business_symbols)} 条；业务明文字符串命中 {len(business_string_hits)} 条。",
        "- `PhoneMsgReceiver / OnReceiveLogin / F_SDKLogin / GameLoginBridge / F_GetServerList` 等关键业务名未在 `libil2cpp.so` 明文中裸露，说明这些名字主要留在 `global-metadata.dat`。",
        "- 继续定位 `PhoneReceiver.PhoneMsgReceiver.OnReceiveLogin` 的真实方法体，需要把 `global-metadata.dat`、metadata registration 与 `libil2cpp.so` 代码地址对齐；这正是 Cpp2IL / Il2CppDumper / Ghidra 脚本一类工具要做的事情。",
        "",
        "## ELF 摘要",
        "",
        "| file | size | machine | entry | sections | dynsym | funcs | symtab | needed | note |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in summary_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('relative_path', ''))} | "
            f"{_markdown_table_cell(row.get('size', ''))} | "
            f"{_markdown_table_cell(row.get('machine', ''))} | "
            f"{_markdown_table_cell(row.get('entry', ''))} | "
            f"{_markdown_table_cell(row.get('section_count', ''))} | "
            f"{_markdown_table_cell(row.get('dynsym_total', ''))} | "
            f"{_markdown_table_cell(row.get('dynsym_funcs', ''))} | "
            f"{_markdown_table_cell(row.get('has_symtab', ''))} | "
            f"{_markdown_table_cell(row.get('needed', ''), limit=180)} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=260)} |"
        )

    lines.extend(["", "## 关键节", "", "| file | index | name | addr | offset | size | note |", "| --- | ---: | --- | --- | --- | --- | --- |"])
    for row in section_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('relative_path', ''))} | "
            f"{_markdown_table_cell(row.get('index', ''))} | "
            f"{_markdown_table_cell(row.get('name', ''))} | "
            f"{_markdown_table_cell(row.get('addr', ''))} | "
            f"{_markdown_table_cell(row.get('offset', ''))} | "
            f"{_markdown_table_cell(row.get('size', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=240)} |"
        )

    lines.extend(["", "## IL2CPP runtime 符号样例", "", "| source | name | value | size | note |", "| --- | --- | --- | ---: | --- |"])
    for row in symbol_rows[:80]:
        lines.append(
            f"| {_markdown_table_cell(row.get('source', ''))} | "
            f"`{_markdown_table_cell(row.get('name', ''), limit=180)}` | "
            f"{_markdown_table_cell(row.get('value', ''))} | "
            f"{_markdown_table_cell(row.get('size', ''))} | "
            f"{_markdown_table_cell(row.get('note', ''), limit=220)} |"
        )

    lines.extend(["", "## 业务明文字符串扫描", "", "| abi | term | count | first_offsets |", "| --- | --- | ---: | --- |"])
    for row in string_rows:
        lines.append(
            f"| {_markdown_table_cell(row.get('abi', ''))} | "
            f"{_markdown_table_cell(row.get('term', ''))} | "
            f"{_markdown_table_cell(row.get('count', ''))} | "
            f"{_markdown_table_cell(row.get('first_offsets', ''), limit=180)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_apk_il2cpp_binary_boundary_probe(
    *,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    root = resolve_fanxiu_apk_unpacked_root(apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, object]] = []
    section_rows: list[dict[str, object]] = []
    symbol_rows: list[dict[str, object]] = []
    lib_paths = sorted((root / "lib").glob("*/libil2cpp.so"), key=lambda item: item.as_posix().lower()) if (root / "lib").is_dir() else []
    for lib_path in lib_paths:
        summary, sections, symbols = _parse_libil2cpp_elf(lib_path)
        summary["relative_path"] = lib_path.relative_to(root).as_posix()
        for row in sections:
            row["relative_path"] = lib_path.relative_to(root).as_posix()
        for row in symbols:
            row["relative_path"] = lib_path.relative_to(root).as_posix()
        summary_rows.append(summary)
        section_rows.extend(sections)
        symbol_rows.extend(symbols)
    string_rows = _collect_il2cpp_boundary_string_hits(root)

    summary_count = _write_tsv(
        output_dir / "apk_il2cpp_binary_boundary_summary.tsv",
        [
            "relative_path",
            "size",
            "is_elf",
            "elf_class",
            "machine",
            "entry",
            "section_count",
            "program_header_count",
            "has_symtab",
            "dynsym_total",
            "dynsym_named",
            "dynsym_funcs",
            "dynsym_il2cpp_exports",
            "needed",
            "note",
        ],
        summary_rows,
    )
    section_count = _write_tsv(
        output_dir / "apk_il2cpp_binary_boundary_sections.tsv",
        ["relative_path", "index", "name", "type", "addr", "offset", "size", "entsize", "link", "note"],
        section_rows,
    )
    symbol_count = _write_tsv(
        output_dir / "apk_il2cpp_binary_boundary_symbols.tsv",
        ["relative_path", "source", "index", "name", "type", "bind", "section_index", "value", "size", "category", "note"],
        symbol_rows,
    )
    string_count = _write_tsv(
        output_dir / "apk_il2cpp_binary_boundary_string_hits.tsv",
        ["abi", "relative_path", "term", "count", "first_offsets", "note"],
        string_rows,
    )
    _write_il2cpp_binary_boundary_markdown(
        output_dir / "apk_il2cpp_binary_boundary_report.md",
        apk_root=root,
        output_dir=output_dir,
        summary_rows=summary_rows,
        section_rows=section_rows,
        symbol_rows=symbol_rows,
        string_rows=string_rows,
    )

    result = {
        "apk_root": str(root),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {
            "libraries": summary_count,
            "sections": section_count,
            "symbols": symbol_count,
            "string_terms": string_count,
            "business_symbols": sum(1 for row in symbol_rows if row.get("category") == "business_symbol"),
            "business_string_hits": sum(1 for row in string_rows if int(row.get("count") or 0) > 0 and row.get("term") != "UnityWebRequest"),
            "has_symtab_libraries": sum(1 for row in summary_rows if int(row.get("has_symtab") or 0) > 0),
        },
        "outputs": {
            "summary": str(output_dir / "apk_il2cpp_binary_boundary_report.json"),
            "markdown": str(output_dir / "apk_il2cpp_binary_boundary_report.md"),
            "elf_summary": str(output_dir / "apk_il2cpp_binary_boundary_summary.tsv"),
            "sections": str(output_dir / "apk_il2cpp_binary_boundary_sections.tsv"),
            "symbols": str(output_dir / "apk_il2cpp_binary_boundary_symbols.tsv"),
            "string_hits": str(output_dir / "apk_il2cpp_binary_boundary_string_hits.tsv"),
        },
    }
    (output_dir / "apk_il2cpp_binary_boundary_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_CPP2IL_LOGIN_DIFFABLE_CSCALLLUAMGR = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/Core/Managers/CsCallLuaMgr.cs"
)
_CPP2IL_LOGIN_ISIL_CSCALLLUAMGR = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Managers/CsCallLuaMgr.txt"
)
_CPP2IL_LOGIN_ISIL_PHONERECEIVER = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/PhoneReceiver/PhoneMsgReceiver.txt"
)


def _find_lua_text_asset(export_base: Path, filename: str, required_terms: tuple[str, ...]) -> Path | None:
    lua_root = export_base / "by_source" / "lscripts"
    if not lua_root.is_dir():
        return None
    for path in sorted(lua_root.rglob(filename), key=lambda item: item.as_posix().lower()):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if all(term in text for term in required_terms):
            return path
    return None


def _append_matching_evidence(
    rows: list[dict[str, object]],
    *,
    path: Path | None,
    export_base: Path,
    kind: str,
    markers: tuple[str, ...],
    stage: str,
) -> None:
    if path is None or not path.is_file():
        for marker in markers:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": "<missing>",
                    "line": "",
                    "marker": marker,
                    "snippet": "missing source file",
                }
            )
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        rows.append(
            {
                "stage": stage,
                "kind": kind,
                "source": _lua_report_source(path, export_base),
                "line": "",
                "marker": "",
                "snippet": f"read failed: {exc}",
            }
        )
        return
    for marker in markers:
        matched = False
        for index, line in enumerate(lines, start=1):
            if marker not in line:
                continue
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _lua_report_source(path, export_base),
                    "line": index,
                    "marker": marker,
                    "snippet": line.strip(),
                }
            )
            matched = True
            break
        if not matched:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _lua_report_source(path, export_base),
                    "line": "",
                    "marker": marker,
                    "snippet": "marker not found",
                }
            )


def _append_matching_evidence_in_section(
    rows: list[dict[str, object]],
    *,
    path: Path | None,
    export_base: Path,
    kind: str,
    section_marker: str,
    markers: tuple[str, ...],
    stage: str,
    end_prefixes: tuple[str, ...] = ("Method: ",),
    end_markers: tuple[str, ...] = (),
) -> None:
    if path is None or not path.is_file():
        for marker in markers:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": "<missing>",
                    "line": "",
                    "marker": marker,
                    "snippet": "missing source file",
                }
            )
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        rows.append(
            {
                "stage": stage,
                "kind": kind,
                "source": _lua_report_source(path, export_base),
                "line": "",
                "marker": "",
                "snippet": f"read failed: {exc}",
            }
        )
        return

    start_index = next((index for index, line in enumerate(lines) if section_marker in line), None)
    if start_index is None:
        for marker in markers:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _lua_report_source(path, export_base),
                    "line": "",
                    "marker": marker,
                    "snippet": f"section not found: {section_marker}",
                }
            )
        return

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        if any(lines[index].startswith(prefix) for prefix in end_prefixes) or any(
            marker in lines[index] for marker in end_markers
        ):
            end_index = index
            break
    section_lines = lines[start_index:end_index]
    for marker in markers:
        matched = False
        for offset, line in enumerate(section_lines, start=start_index + 1):
            if marker not in line:
                continue
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _lua_report_source(path, export_base),
                    "line": offset,
                    "marker": marker,
                    "snippet": line.strip(),
                }
            )
            matched = True
            break
        if not matched:
            rows.append(
                {
                    "stage": stage,
                    "kind": kind,
                    "source": _lua_report_source(path, export_base),
                    "line": "",
                    "marker": marker,
                    "snippet": f"marker not found in section: {section_marker}",
                }
            )


def _evidence_has(rows: list[dict[str, object]], marker: str) -> bool:
    return any(row.get("marker") == marker and row.get("line") not in {"", None} for row in rows)


def _evidence_has_stage(rows: list[dict[str, object]], stage: str, marker: str) -> bool:
    return any(
        row.get("stage") == stage and row.get("marker") == marker and row.get("line") not in {"", None}
        for row in rows
    )


def _write_cpp2il_login_lua_bridge_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Cpp2IL login-to-Lua bridge report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- Cpp2IL has now filled the previous gap between `PhoneMsgReceiver.OnReceiveLoginData` and Lua login logic.",
        "- `CsCallLuaMgr.GetLoginTokenSucceed(LoginToken, GameId, Pid, Uid, TimeStamp)` uses the cached `_LoginMgr` Lua table and dispatches the Lua `GetLoginTokenSucceed` function with the same five arguments.",
        "- Lua `LoginMgr.GetLoginTokenSucceed` immediately calls `LoginMgr.Inst_get():LoginCheck(...)`.",
        "- The later Lua path calls `GetServerInfo:GetSDKServerList(Pid, Token)`, which encodes `pid/token/bundleId/bundleVersion/gzip/cid/gid` and invokes `GameLoginBridge.F_GetServerList(callbackId, jsonStr, isZip)`.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "PhoneMsgReceiver.OnReceiveLoginData(data)",
        "  -> CsCallLuaMgr.GetLoginTokenSucceed(LoginToken, GameId, Pid, Uid, TimeStamp)",
        "  -> _LoginMgr[\"GetLoginTokenSucceed\"](LoginToken, GameId, Pid, Uid, TimeStamp)",
        "  -> LoginMgr.Inst_get():LoginCheck(LoginToken, GameId, ChannelId, Uid, Timestamp)",
        "  -> LoginMgr:GetSDKServerInfo(Pid, Token)",
        "  -> GetServerInfo:GetSDKServerList(Pid, Token)",
        "  -> GameLoginBridge.F_GetServerList(callbackId, jsonStr, isZip)",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'cpp2il_login_lua_bridge_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'cpp2il_login_lua_bridge_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_cpp2il_login_lua_bridge_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Connect the recovered Cpp2IL login callback to Lua server-list logic."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    diffable_cs = output_dir / _CPP2IL_LOGIN_DIFFABLE_CSCALLLUAMGR
    cs_isil = output_dir / _CPP2IL_LOGIN_ISIL_CSCALLLUAMGR
    phone_isil = output_dir / _CPP2IL_LOGIN_ISIL_PHONERECEIVER
    login_mgr_lua = _find_lua_text_asset(export_base, "LoginMgr.lua", ("GetLoginTokenSucceed", "GetSDKServerInfo"))
    get_server_info_lua = _find_lua_text_asset(export_base, "GetServerInfo.lua", ("GetSDKServerList", "F_GetServerList"))

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence(
        evidence_rows,
        path=phone_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="01-il2cpp-receiver",
        markers=(
            "Method: System.Void OnReceiveLoginData(System.String data)",
            "Call String.Split",
            "Call CsCallLuaMgr.GetLoginTokenSucceed",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=diffable_cs,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="02-cs-to-lua-cache",
        markers=(
            "private static LuaTable _LoginMgr; //Field offset: 0x20",
            "public static void GetLoginTokenSucceed(string LoginToken, string GameId, string Pid, string Uid, string TimeStamp)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=cs_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="03-cs-to-lua-call",
        markers=(
            "Method: System.Void GetLoginTokenSucceed(System.String LoginToken, System.String GameId, System.String Pid, System.String Uid, System.String TimeStamp)",
            "Call LuaBaseRef.op_Inequality",
            "Call LuaTable.get_Item",
            "Call 0x1284E04, X0, X1, X2, X3, X4, X5",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua",
        stage="04-lua-loginmgr",
        markers=(
            "function _M.GetLoginTokenSucceed(LoginToken,GameId,ChannelId,Uid,Timestamp)",
            "LoginMgr.Inst_get():LoginCheck(LoginToken,GameId,ChannelId,Uid,Timestamp)",
            "function _M.GetSDKServerInfo(self,Pid,Token)",
            "self.GetServerInfo:GetSDKServerList(Pid,Token)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=get_server_info_lua,
        export_base=export_base,
        kind="lua",
        stage="05-server-list-request",
        markers=(
            "function _M.GetSDKServerList(self,Pid,LoginToken)",
            "pid=Pid,",
            "token=LoginToken,",
            "cid=PhoneHelper.GetPid(),",
            "gid=PhoneHelper.GetGameId(),",
            "GameLoginBridge.F_GetServerList(serverListCallbackId,jsonStr,isZip)",
        ),
    )

    checks = {
        "phone_receiver_isil": phone_isil.is_file(),
        "cscallluamgr_diffable_cs": diffable_cs.is_file(),
        "cscallluamgr_isil": cs_isil.is_file(),
        "login_mgr_lua": login_mgr_lua is not None and login_mgr_lua.is_file(),
        "get_server_info_lua": get_server_info_lua is not None and get_server_info_lua.is_file(),
        "cpp2il_calls_lua_getlogin": _evidence_has(
            evidence_rows,
            "Call 0x1284E04, X0, X1, X2, X3, X4, X5",
        ),
        "lua_callback_enters_logincheck": _evidence_has(
            evidence_rows,
            "LoginMgr.Inst_get():LoginCheck(LoginToken,GameId,ChannelId,Uid,Timestamp)",
        ),
        "lua_server_list_uses_pid_token": _evidence_has(evidence_rows, "pid=Pid,")
        and _evidence_has(evidence_rows, "token=LoginToken,"),
        "lua_calls_gamelogin_bridge": _evidence_has(
            evidence_rows,
            "GameLoginBridge.F_GetServerList(serverListCallbackId,jsonStr,isZip)",
        ),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "cpp2il_login_lua_bridge_evidence.tsv", fields, evidence_rows)
    _write_cpp2il_login_lua_bridge_markdown(
        output_dir / "cpp2il_login_lua_bridge_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "cpp2il_login_lua_bridge_report.md"),
        "evidence_path": str(output_dir / "cpp2il_login_lua_bridge_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "phone_receiver_isil": str(phone_isil),
            "cscallluamgr_diffable_cs": str(diffable_cs),
            "cscallluamgr_isil": str(cs_isil),
            "login_mgr_lua": str(login_mgr_lua) if login_mgr_lua else "",
            "get_server_info_lua": str(get_server_info_lua) if get_server_info_lua else "",
        },
    }
    (output_dir / "cpp2il_login_lua_bridge_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_fanxiu_login_token_to_socket_handoff_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    field_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Login token to socket handoff report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report joins the Java SDK callback, IL2CPP receiver, Lua server-list request, and Lua socket-login packet surfaces into one static map.",
        "- The observed Android shell path emits `1__token__gid__pid`; IL2CPP also has optional `Uid/TimeStamp` slots and forwards five arguments to Lua.",
        "- The socket `CM_Login(20001)` does not directly write the original Java payload string. It reconstructs fields from `curServerItem`, `PhoneHelper`, and `LoginModel:GetLoginAccountData()` before `SocketManager:F_SendMsg`.",
        "- Static-only safety boundary: this report records field names and code paths only. It does not capture live tokens, produce signatures, replay requests, or modify the APK.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "FlameUnityActivity.SDKLogin(Bundle token/gid/pid)",
        "  -> SDKCallback.OnSDKLoginData(\"1__token__gid__pid\")",
        "  -> UnityPlayer.UnitySendMessage(GameEnter, OnReceiveLogin, data)",
        "  -> PhoneMsgReceiver.OnReceiveLoginData(data)",
        "  -> AccountInfo.V_LoginToken / V_GameId / V_PId",
        "  -> CsCallLuaMgr.GetLoginTokenSucceed(LoginToken, GameId, Pid, Uid, TimeStamp)",
        "  -> LoginMgr:GetSDKServerInfo(Pid, Token)",
        "  -> GetServerInfo:GetSDKServerList(Pid, LoginToken)",
        "  -> LoginData.ServerVoInfo / curServerItem",
        "  -> EnterGameInfo:ContinueLogin()",
        "  -> LoginNetLogic.CM_LoginFun(serverId, account, pid, channelPackage)",
        "  -> CM_Login.writing()",
        "  -> SocketManager:F_SendMsg(CM_Login)",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Field Source Map",
            "",
            "| Stage | Field | Source | Downstream |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        stage = str(row.get("stage", "")).replace("|", "\\|")
        field = str(row.get("field", "")).replace("|", "\\|")
        source = str(row.get("source_expr", "")).replace("|", "\\|")
        downstream = str(row.get("downstream", "")).replace("|", "\\|")
        lines.append(f"| {stage} | `{field}` | `{source}` | {downstream} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'login_token_to_socket_handoff_evidence.tsv', export_base)}`",
            f"- Field source TSV: `{_lua_report_source(output_dir / 'login_token_to_socket_handoff_fields.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'login_token_to_socket_handoff_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_login_token_to_socket_handoff_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Join Java/IL2CPP/Lua evidence for the login token handoff into CM_Login."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    jadx_activity = (
        output_dir
        / "jadx_1_5_5_nores"
        / "sources"
        / "com"
        / "flamePhoenix"
        / "plugin"
        / "activity"
        / "FlameUnityActivity.java"
    )
    phone_isil = output_dir / _CPP2IL_LOGIN_ISIL_PHONERECEIVER
    account_info_cs = (
        output_dir
        / "cpp2il_2022_1_pre21_arm64_diffable_cs"
        / "DiffableCs"
        / "Assembly-CSharp"
        / "MU"
        / "Tool"
        / "AccountInfo.cs"
    )
    cscalllua_cs = output_dir / _CPP2IL_LOGIN_DIFFABLE_CSCALLLUAMGR
    login_mgr_lua = _find_lua_text_asset(export_base, "LoginMgr.lua", ("GetLoginTokenSucceed", "GetSDKServerInfo"))
    get_server_info_lua = _find_lua_text_asset(export_base, "GetServerInfo.lua", ("GetSDKServerList", "F_GetServerList"))
    enter_game_info_lua = _find_lua_text_asset(export_base, "EnterGameInfo.lua", ("ContinueLogin", "CM_LoginFun"))
    login_net_logic_lua = _find_lua_text_asset(export_base, "LoginNetLogic.lua", ("CM_LoginFun", "CM_Login.sign"))
    cm_login_lua = _find_lua_text_asset(export_base, "CM_Login.lua", ("self:writeString(self.account)", "return 20001"))
    socket_manager_lua = _find_lua_text_asset(export_base, "SocketManager.lua", ("F_SendMsg", "DoSendMsg"))

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence(
        evidence_rows,
        path=jadx_activity,
        export_base=export_base,
        kind="jadx_java",
        stage="01-java-sdk-payload",
        markers=(
            'String string = bundle.getString("token");',
            'FlameUnityActivity.m_gid = bundle.getString("gid");',
            "FlameUnityActivity.m_pid = bundle.getString(PushConsts.KEY_SERVICE_PIT);",
            'SDKCallback.OnSDKLoginData("1__" + string + "__" + FlameUnityActivity.m_gid + "__" + FlameUnityActivity.m_pid);',
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=phone_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="02-il2cpp-receiver",
        markers=(
            "Call String.Split",
            "Call Int32.Parse",
            "Move [X8+16], X22",
            "Move [X8+24], X21",
            "Move [X8+32], X20",
            "Move [X8+56], X23",
            "Call CsCallLuaMgr.GetLoginTokenSucceed",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=account_info_cs,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="03-account-info-fields",
        markers=(
            "public string V_LoginToken; //Field offset: 0x10",
            "public string V_GameId; //Field offset: 0x18",
            "public string V_PId; //Field offset: 0x20",
            "public string V_UId; //Field offset: 0x38",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=cscalllua_cs,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="04-cs-to-lua-bridge",
        markers=(
            "public static void GetLoginTokenSucceed(string LoginToken, string GameId, string Pid, string Uid, string TimeStamp)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="05-lua-token-entry",
        markers=(
            "function _M.GetLoginTokenSucceed(LoginToken,GameId,ChannelId,Uid,Timestamp)",
            "LoginMgr.Inst_get():LoginCheck(LoginToken,GameId,ChannelId,Uid,Timestamp)",
            "function _M.GetSDKServerInfo(self,Pid,Token)",
            "self.GetServerInfo:GetSDKServerList(Pid,Token)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=get_server_info_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="06-server-list-request",
        markers=(
            "function _M.GetSDKServerList(self,Pid,LoginToken)",
            "pid=Pid,",
            "token=LoginToken,",
            "cid=PhoneHelper.GetPid(),",
            "gid=PhoneHelper.GetGameId(),",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=enter_game_info_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="07-enter-game-continue",
        markers=(
            "local curServerItem=LoginMgr.Inst_get():GetCurServerItem()",
            "LoginMgr.Inst_get().LoginNetLogic.CM_ReLoginHandler(curServerItem.account,curServerItem.serverId,curServerItem.token,curServerItem.pid,self.V_ChannelPackage)",
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFun(curServerItem.serverId,curServerItem.account,curServerItem.pid,self.V_ChannelPackage)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_net_logic_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="08-cm-login-field-fill",
        markers=(
            "function _M.CM_LoginFun(serverId,account,pid,channelPackage)",
            "CM_Login.serverId=serverId",
            "CM_Login.account=account",
            'CM_Login.pid=pid or""',
            "local loginAccount=LoginMgr.Inst_get().LoginModel:GetLoginAccountData()",
            'CM_Login.sign=loginAccount and loginAccount.V_Token or""',
            "CM_Login.signTime=loginAccount and loginAccount.V_Time or 0",
            'CM_Login.cid=(PhoneHelper.GetPid()or"")..""',
            'CM_Login.gid=(PhoneHelper.GetGameId()or"")..""',
            "SocketManager.Inst_get():F_SendMsg(CM_Login)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=cm_login_lua,
        export_base=export_base,
        kind="lua_packet",
        stage="09-cm-login-wire-order",
        markers=(
            "self:writeString(self.account)",
            "self:writeInt(self.serverId)",
            "self:writeString(self.pid)",
            "self:writeString(self.cid)",
            "self:writeString(self.gid)",
            "self:writeInt(self.signTime)",
            "self:writeString(self.sign)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=socket_manager_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="10-socket-send-sink",
        markers=(
            "function _M.F_SendMsg(self,pSendInfo,clientData)",
            "pSendInfo:write()",
            "so:F_Send(pid,sn)",
        ),
    )

    field_rows = [
        {
            "stage": "java_payload",
            "field": "status",
            "source_expr": '"1"',
            "downstream": "IL2CPP parses `parts[0]` with `Int32.Parse`; non-zero enters success branch.",
        },
        {
            "stage": "java_payload",
            "field": "token",
            "source_expr": 'bundle.getString("token")',
            "downstream": "`AccountInfo.V_LoginToken`; Lua `LoginToken/Token`; server-list `token=LoginToken`; later login-account sign source.",
        },
        {
            "stage": "java_payload",
            "field": "gid",
            "source_expr": 'bundle.getString("gid")',
            "downstream": "`AccountInfo.V_GameId`; Lua `GameId`; `PhoneHelper.GetGameId()` feeds server-list `gid` and `CM_Login.gid`.",
        },
        {
            "stage": "java_payload",
            "field": "pid",
            "source_expr": "bundle.getString(PushConsts.KEY_SERVICE_PIT)",
            "downstream": "`AccountInfo.V_PId`; Lua `Pid`; server-list `pid=Pid`; selected server item feeds `CM_Login.pid`.",
        },
        {
            "stage": "il2cpp_optional",
            "field": "uid/timestamp",
            "source_expr": "optional split slots after pid",
            "downstream": "Cpp2IL shows `Uid/TimeStamp` parameters to Lua, but the observed Java shell login path only emits token/gid/pid.",
        },
        {
            "stage": "server_list",
            "field": "pid/token/cid/gid",
            "source_expr": "GetServerInfo:GetSDKServerList(Pid, LoginToken)",
            "downstream": "Server-list response creates `curServerItem` with host/port/account/pid/serverId/token.",
        },
        {
            "stage": "cm_login",
            "field": "account/serverId/pid",
            "source_expr": "curServerItem.account/serverId/pid",
            "downstream": "`CM_LoginFun` copies these before packet write order.",
        },
        {
            "stage": "cm_login",
            "field": "sign/signTime",
            "source_expr": "LoginModel:GetLoginAccountData().V_Token / V_Time",
            "downstream": "`CM_Login.signTime` then `CM_Login.sign` are the final two wire fields.",
        },
        {
            "stage": "cm_login",
            "field": "cid/gid/device/bundle/devId/pushToken",
            "source_expr": "PhoneHelper getters",
            "downstream": "Device/channel metadata are filled locally immediately before `SocketManager:F_SendMsg`.",
        },
    ]

    checks = {
        "java_payload_emits_token_gid_pid": _evidence_has(
            evidence_rows,
            'SDKCallback.OnSDKLoginData("1__" + string + "__" + FlameUnityActivity.m_gid + "__" + FlameUnityActivity.m_pid);',
        ),
        "il2cpp_receiver_splits_and_parses_status": _evidence_has(evidence_rows, "Call String.Split")
        and _evidence_has(evidence_rows, "Call Int32.Parse"),
        "il2cpp_writes_accountinfo_token_gid_pid": _evidence_has(evidence_rows, "Move [X8+16], X22")
        and _evidence_has(evidence_rows, "Move [X8+24], X21")
        and _evidence_has(evidence_rows, "Move [X8+32], X20"),
        "accountinfo_fields_named": _evidence_has(evidence_rows, "public string V_LoginToken; //Field offset: 0x10")
        and _evidence_has(evidence_rows, "public string V_GameId; //Field offset: 0x18")
        and _evidence_has(evidence_rows, "public string V_PId; //Field offset: 0x20"),
        "cs_bridge_keeps_five_login_args": _evidence_has(
            evidence_rows,
            "public static void GetLoginTokenSucceed(string LoginToken, string GameId, string Pid, string Uid, string TimeStamp)",
        ),
        "lua_enters_logincheck_and_serverlist": _evidence_has(
            evidence_rows,
            "LoginMgr.Inst_get():LoginCheck(LoginToken,GameId,ChannelId,Uid,Timestamp)",
        )
        and _evidence_has(evidence_rows, "self.GetServerInfo:GetSDKServerList(Pid,Token)"),
        "serverlist_posts_pid_and_token": _evidence_has(evidence_rows, "pid=Pid,")
        and _evidence_has(evidence_rows, "token=LoginToken,"),
        "normal_login_uses_cur_server_item": _evidence_has(
            evidence_rows,
            "LoginMgr.Inst_get().LoginNetLogic.CM_LoginFun(curServerItem.serverId,curServerItem.account,curServerItem.pid,self.V_ChannelPackage)",
        ),
        "cm_login_sign_from_login_model": _evidence_has(
            evidence_rows,
            "local loginAccount=LoginMgr.Inst_get().LoginModel:GetLoginAccountData()",
        )
        and _evidence_has(evidence_rows, 'CM_Login.sign=loginAccount and loginAccount.V_Token or""'),
        "cm_login_wire_tail_is_sign_time_sign": _evidence_has(evidence_rows, "self:writeInt(self.signTime)")
        and _evidence_has(evidence_rows, "self:writeString(self.sign)"),
        "send_sink_writes_then_sends": _evidence_has(evidence_rows, "pSendInfo:write()")
        and _evidence_has(evidence_rows, "so:F_Send(pid,sn)"),
    }

    evidence_fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    field_fields = ["stage", "field", "source_expr", "downstream"]
    evidence_count = _write_tsv(output_dir / "login_token_to_socket_handoff_evidence.tsv", evidence_fields, evidence_rows)
    field_count = _write_tsv(output_dir / "login_token_to_socket_handoff_fields.tsv", field_fields, field_rows)
    _write_fanxiu_login_token_to_socket_handoff_markdown(
        output_dir / "login_token_to_socket_handoff_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        field_rows=field_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "login_token_to_socket_handoff_report.md"),
        "evidence_path": str(output_dir / "login_token_to_socket_handoff_evidence.tsv"),
        "field_source_path": str(output_dir / "login_token_to_socket_handoff_fields.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence_rows": evidence_count,
            "field_rows": field_count,
        },
        "sources": {
            "jadx_activity": str(jadx_activity),
            "phone_receiver_isil": str(phone_isil),
            "account_info_cs": str(account_info_cs),
            "cscalllua_cs": str(cscalllua_cs),
            "login_mgr_lua": str(login_mgr_lua) if login_mgr_lua else "",
            "get_server_info_lua": str(get_server_info_lua) if get_server_info_lua else "",
            "enter_game_info_lua": str(enter_game_info_lua) if enter_game_info_lua else "",
            "login_net_logic_lua": str(login_net_logic_lua) if login_net_logic_lua else "",
            "cm_login_lua": str(cm_login_lua) if cm_login_lua else "",
            "socket_manager_lua": str(socket_manager_lua) if socket_manager_lua else "",
        },
    }
    (output_dir / "login_token_to_socket_handoff_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_fanxiu_login_account_sign_source_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    field_rows: list[dict[str, object]],
    url_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Login account sign source report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report traces where `CM_Login.sign/signTime` come from on the Lua/client side.",
        "- `LoginMgr:LoginCheck` sends the SDK token plus device/channel metadata through `GameLoginBridge.F_WebLoginCheck`; the returned JSON becomes a `LoginAccount` VO.",
        "- `LoginAccount.FillData` maps `data.token -> V_Token` and `data.time -> V_Time`; `StartGameInfo.LoginAccountDataInfo` stores that VO in `LoginModel.V_LoginAccount`.",
        "- `LoginNetLogic.CM_LoginFun` later copies `LoginModel:GetLoginAccountData().V_Token / V_Time` into `CM_Login.sign/signTime`.",
        "- Static-only safety boundary: this records fields and call paths only. It does not generate signatures, replay login checks, or store live account tokens.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "LoginMgr.GetLoginTokenSucceed(LoginToken, GameId, ChannelId, Uid, Timestamp)",
        "  -> LoginMgr:LoginCheck(LoginToken, GameId, ChannelId, Uid, TimeStamp)",
        "  -> data = { token, uid, bundleId, bundleVersion, cid, device, devId, gid, time }",
        "  -> GameLoginBridge.F_WebLoginCheck(callbackId, LoginToken, LuaUtil.encode(data))",
        "  -> FileUtil.F_LoadFilePost(login-check-url, jsonData, success, error)",
        "  -> CallBackManager.CallStringDelegate(callbackId, responseText)",
        "  -> LoginMgr:StartInfo(jsonData)",
        "  -> StartGameInfo:LoginAccountDataInfo(jsonData)",
        "  -> LuaUtil.decode(jsonData, typeof(LoginAccount))",
        "  -> LoginAccount.FillData: V_Token=data.token, V_Time=data.time",
        "  -> LoginModel:SetLoginAccountData(loginAccount)",
        "  -> LoginNetLogic.CM_LoginFun: CM_Login.sign/signTime",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Field Source Map",
            "",
            "| Stage | Field | Source | Downstream |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in field_rows:
        stage = str(row.get("stage", "")).replace("|", "\\|")
        field = str(row.get("field", "")).replace("|", "\\|")
        source = str(row.get("source_expr", "")).replace("|", "\\|")
        downstream = str(row.get("downstream", "")).replace("|", "\\|")
        lines.append(f"| {stage} | `{field}` | `{source}` | {downstream} |")

    lines.extend(
        [
            "",
            "## Login Check URL Config",
            "",
            "| Source | Key | Value | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in url_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        key = str(row.get("key", "")).replace("|", "\\|")
        value = str(row.get("value", "")).replace("|", "\\|")
        note = str(row.get("note", "")).replace("|", "\\|")
        lines.append(f"| `{source}` | `{key}` | `{value}` | {note} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'login_account_sign_source_evidence.tsv', export_base)}`",
            f"- Field source TSV: `{_lua_report_source(output_dir / 'login_account_sign_source_fields.tsv', export_base)}`",
            f"- URL TSV: `{_lua_report_source(output_dir / 'login_account_sign_source_urls.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'login_account_sign_source_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_login_account_sign_source_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Trace the LoginAccount fields that later feed CM_Login.sign/signTime."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    bridge_cs = output_dir / _CPP2IL_GAMELOGIN_DIFFABLE
    bridge_isil = output_dir / _CPP2IL_GAMELOGIN_ISIL
    web_login_callback_isil = (
        output_dir
        / "cpp2il_2022_1_pre21_arm64_isil"
        / "IsilDump"
        / "Assembly-CSharp"
        / "LuaBridge"
        / "Login"
        / "GameLoginBridge_NestedType___c__DisplayClass2_0.txt"
    )
    setting_constant_cs = output_dir / _CPP2IL_SETTING_CONSTANT_DIFFABLE
    login_mgr_lua = _find_lua_text_asset(export_base, "LoginMgr.lua", ("LoginCheck", "F_WebLoginCheck"))
    start_game_info_lua = _find_lua_text_asset(export_base, "StartGameInfo.lua", ("LoginAccountDataInfo", "SetLoginAccountData"))
    login_model_lua = _find_lua_text_asset(export_base, "LoginModel.lua", ("SetLoginAccountData", "GetLoginAccountData"))
    login_account_lua = _find_lua_text_asset(export_base, "LoginAccount.lua", ("V_Token", "V_Time", "FillData"))
    login_net_logic_lua = _find_lua_text_asset(export_base, "LoginNetLogic.lua", ("CM_Login.sign", "CM_Login.signTime"))

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="01-lua-login-check-request",
        markers=(
            "function _M.LoginCheck(self,LoginToken,GameId,ChannelId,Uid,TimeStamp)",
            "data.token=LoginToken",
            "data.uid=Uid",
            "data.time=os.time()",
            "GameLoginBridge.F_WebLoginCheck(loginCallbackId,LoginToken,str)",
            "self:StartInfo(jsonData)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="02-lua-start-info",
        markers=(
            "function _M.StartInfo(self,jsonData)",
            "self.StartGameInfo:LoginAccountDataInfo(jsonData)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=bridge_cs,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="03-cpp2il-bridge-signature",
        markers=("public static void F_WebLoginCheck(int callbackid, string token, string jsonData) { }",),
    )
    _append_matching_evidence(
        evidence_rows,
        path=setting_constant_cs,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="04-url-setting-constants",
        markers=("ImportServerUrl = 12,", "TestLoginCheckInUrl = 13,"),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=bridge_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="05-cpp2il-web-login-check-request",
        section_marker="Method: System.Void F_WebLoginCheck(System.Int32 callbackid, System.String token, System.String jsonData)",
        markers=(
            "Method: System.Void F_WebLoginCheck(System.Int32 callbackid, System.String token, System.String jsonData)",
            "Call String.IsNullOrEmpty",
            "Move W1, 13",
            "Or W1, W31, 12",
            "Call GameInitSettingModel.F_GetSettingValue, X0, X1",
            "Call String.Concat, X0, X1, X2, X3",
            "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=web_login_callback_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="06-cpp2il-web-login-check-callback",
        section_marker="Method: System.Void <F_WebLoginCheck>b__0(UnityEngine.Networking.DownloadHandler www)",
        markers=(
            "Method: System.Void <F_WebLoginCheck>b__0(UnityEngine.Networking.DownloadHandler www)",
            "Call DownloadHandler.get_text, X0",
            "Call CallBackManager.CallStringDelegate, X0, X1",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=start_game_info_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="07-lua-decode-login-account",
        markers=(
            "function _M.LoginAccountDataInfo(self,jsondata)",
            'local LoginAccount=require"GameSystem.Game.Message.module.user.login.packet.vo.LoginAccount"',
            "loginAccount=LuaUtil.decode(jsondata,typeof(LoginAccount))",
            "LoginMgr.Inst_get().LoginModel:SetLoginAccountData(loginAccount)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_account_lua,
        export_base=export_base,
        kind="lua_vo",
        stage="08-login-account-vo-fields",
        markers=(
            "function _M.FillData(self,data)",
            "self.V_Time=self.V_Data.time",
            "self.V_Token=self.V_Data.token",
            "self.V_UId=self.V_Data.uid",
            "self.V_PId=self.V_Data.pid",
            "self.V_GId=self.V_Data.gid",
            "self.V_CId=self.V_Data.cid",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_model_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="09-login-model-cache",
        markers=(
            "function _M.SetLoginAccountData(self,loginAccount)",
            "self.V_LoginAccount=loginAccount",
            "function _M.GetLoginAccountData(self)",
            "return self.V_LoginAccount",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_net_logic_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="10-cm-login-sign-consumer",
        markers=(
            "local loginAccount=LoginMgr.Inst_get().LoginModel:GetLoginAccountData()",
            'CM_Login.sign=loginAccount and loginAccount.V_Token or""',
            "CM_Login.signTime=loginAccount and loginAccount.V_Time or 0",
        ),
    )

    field_rows = [
        {
            "stage": "web_login_check_request",
            "field": "token",
            "source_expr": "LoginToken",
            "downstream": "`GameLoginBridge.F_WebLoginCheck(callbackId, LoginToken, json)` and request body `data.token`.",
        },
        {
            "stage": "web_login_check_request",
            "field": "uid",
            "source_expr": "Uid",
            "downstream": "Request body `data.uid`; response later maps to `LoginAccount.V_UId`.",
        },
        {
            "stage": "web_login_check_request",
            "field": "time",
            "source_expr": "os.time()",
            "downstream": "Client request timestamp; response `data.time` becomes `LoginAccount.V_Time`.",
        },
        {
            "stage": "login_account_response",
            "field": "V_Token",
            "source_expr": "LoginAccount.FillData: data.token",
            "downstream": "`LoginModel.V_LoginAccount.V_Token` -> `CM_Login.sign`.",
        },
        {
            "stage": "login_account_response",
            "field": "V_Time",
            "source_expr": "LoginAccount.FillData: data.time",
            "downstream": "`LoginModel.V_LoginAccount.V_Time` -> `CM_Login.signTime`.",
        },
        {
            "stage": "login_account_response",
            "field": "V_PId/V_GId/V_CId/V_UId",
            "source_expr": "LoginAccount.FillData: data.pid/gid/cid/uid",
            "downstream": "Account/server selection context and analytics; `pid` can also feed selected server item.",
        },
    ]

    url_rows = []
    for row in _read_tsv_rows(output_dir / "apk_download_config_entries.tsv"):
        if row.get("key") in {"ImportServerUrl", "TestLoginCheckInUrl"}:
            url_rows.append(
                {
                    "source": row.get("source", ""),
                    "key": row.get("key", ""),
                    "value": row.get("value", ""),
                    "note": row.get("note", ""),
                }
            )
    url_rows.sort(key=lambda item: (str(item.get("source", "")), str(item.get("key", ""))))

    checks = {
        "lua_logincheck_builds_web_login_payload": _evidence_has(evidence_rows, "data.token=LoginToken")
        and _evidence_has(evidence_rows, "data.time=os.time()")
        and _evidence_has(evidence_rows, "GameLoginBridge.F_WebLoginCheck(loginCallbackId,LoginToken,str)"),
        "cpp2il_web_logincheck_uses_import_or_test_url": _evidence_has(evidence_rows, "Move W1, 13")
        and _evidence_has(evidence_rows, "Or W1, W31, 12")
        and _evidence_has(evidence_rows, "Call GameInitSettingModel.F_GetSettingValue, X0, X1"),
        "cpp2il_posts_and_returns_text_to_lua": _evidence_has(evidence_rows, "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3")
        and _evidence_has(evidence_rows, "Call DownloadHandler.get_text, X0")
        and _evidence_has(evidence_rows, "Call CallBackManager.CallStringDelegate, X0, X1"),
        "lua_decodes_loginaccount_vo": _evidence_has(
            evidence_rows,
            "loginAccount=LuaUtil.decode(jsondata,typeof(LoginAccount))",
        ),
        "loginaccount_maps_token_and_time": _evidence_has(evidence_rows, "self.V_Time=self.V_Data.time")
        and _evidence_has(evidence_rows, "self.V_Token=self.V_Data.token"),
        "loginmodel_caches_loginaccount": _evidence_has(evidence_rows, "self.V_LoginAccount=loginAccount")
        and _evidence_has(evidence_rows, "return self.V_LoginAccount"),
        "cm_login_consumes_token_and_time_as_sign": _evidence_has(
            evidence_rows,
            'CM_Login.sign=loginAccount and loginAccount.V_Token or""',
        )
        and _evidence_has(evidence_rows, "CM_Login.signTime=loginAccount and loginAccount.V_Time or 0"),
        "url_config_rows_present": bool(url_rows),
    }

    evidence_fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    field_fields = ["stage", "field", "source_expr", "downstream"]
    url_fields = ["source", "key", "value", "note"]
    evidence_count = _write_tsv(output_dir / "login_account_sign_source_evidence.tsv", evidence_fields, evidence_rows)
    field_count = _write_tsv(output_dir / "login_account_sign_source_fields.tsv", field_fields, field_rows)
    url_count = _write_tsv(output_dir / "login_account_sign_source_urls.tsv", url_fields, url_rows)
    _write_fanxiu_login_account_sign_source_markdown(
        output_dir / "login_account_sign_source_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        field_rows=field_rows,
        url_rows=url_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "login_account_sign_source_report.md"),
        "evidence_path": str(output_dir / "login_account_sign_source_evidence.tsv"),
        "field_source_path": str(output_dir / "login_account_sign_source_fields.tsv"),
        "url_path": str(output_dir / "login_account_sign_source_urls.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "counts": {
            "evidence_rows": evidence_count,
            "field_rows": field_count,
            "url_rows": url_count,
        },
        "sources": {
            "bridge_cs": str(bridge_cs),
            "bridge_isil": str(bridge_isil),
            "web_login_callback_isil": str(web_login_callback_isil),
            "setting_constant_cs": str(setting_constant_cs),
            "login_mgr_lua": str(login_mgr_lua) if login_mgr_lua else "",
            "start_game_info_lua": str(start_game_info_lua) if start_game_info_lua else "",
            "login_model_lua": str(login_model_lua) if login_model_lua else "",
            "login_account_lua": str(login_account_lua) if login_account_lua else "",
            "login_net_logic_lua": str(login_net_logic_lua) if login_net_logic_lua else "",
        },
    }
    (output_dir / "login_account_sign_source_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_CPP2IL_GAMELOGIN_DIFFABLE = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/LuaBridge/Login/GameLoginBridge.cs"
)
_CPP2IL_GAMELOGIN_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/LuaBridge/Login/GameLoginBridge.txt"
)
_CPP2IL_GAMELOGIN_NESTED_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/LuaBridge/Login/"
    "GameLoginBridge_NestedType___c__DisplayClass0_0.txt"
)
_CPP2IL_SETTING_CONSTANT_DIFFABLE = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/MU/Define/EM_SettingConstant.cs"
)
_CPP2IL_FILEUTIL_DIFFABLE = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/MU/Common/FileUtil.cs"
)
_CPP2IL_FILEUTIL_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/MU/Common/FileUtil.txt"
)
_CPP2IL_FILEUTIL_POST_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/MU/Common/"
    "FileUtil_NestedType__F_LoadFilePost_d__7.txt"
)


def _write_cpp2il_gamelogin_serverlist_bridge_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Cpp2IL GameLoginBridge server-list bridge report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- `GameLoginBridge.F_GetServerList(callbackid, jsonData, iszip)` is the C#/IL2CPP HTTP bridge used by Lua `GetServerInfo:GetSDKServerList`.",
        "- Cpp2IL ISIL shows it stores `callbackid/iszip` in a closure, reads `EM_SettingConstant.ServerListUrl = 8` through `GameInitSettingModel.F_GetSettingValue`, formats the request text, then posts through `FileUtil.F_LoadFilePost` and starts the coroutine.",
        "- The success callback branches on `iszip`: gzip responses use `DownloadHandler.get_data -> UtilCompress.DecompressFromGzip -> Encoding.UTF8`, while plain responses use `DownloadHandler.get_text`.",
        "- Both success branches return the decoded server-list string to Lua through `CallBackManager.CallStringDelegate(callbackid, text)`. The error callback logs, reports launch-process code `1202`, and calls the same Lua string delegate with the fallback string.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "GetServerInfo:GetSDKServerList(Pid, LoginToken)",
        "  -> GameLoginBridge.F_GetServerList(callbackId, jsonData, isZip)",
        "  -> GameInitSettingModel.F_GetSettingValue(ServerListUrl = 8)",
        "  -> String.Format(settingUrl, jsonData, isZip)",
        "  -> FileUtil.F_LoadFilePost(url, jsonData, successDelegate, errorDelegate)",
        "  -> CoroutineManager.StartCoroutine(...)",
        "  -> <F_GetServerList>b__0(www): decode gzip/text response",
        "  -> CallBackManager.CallStringDelegate(callbackId, responseText)",
        "```",
        "",
        "Known production config evidence still points at `https://prod-login-frxxz.akbing.com/game/server`; the APK also contains older/default `frxxz-test1.eyugame.com` URL config entries.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'cpp2il_gamelogin_serverlist_bridge_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'cpp2il_gamelogin_serverlist_bridge_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_cpp2il_gamelogin_serverlist_bridge_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Recover the IL2CPP HTTP bridge from Lua server-list request to Lua callback."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    gamelogin_diffable = output_dir / _CPP2IL_GAMELOGIN_DIFFABLE
    gamelogin_isil = output_dir / _CPP2IL_GAMELOGIN_ISIL
    gamelogin_nested_isil = output_dir / _CPP2IL_GAMELOGIN_NESTED_ISIL
    setting_constant = output_dir / _CPP2IL_SETTING_CONSTANT_DIFFABLE
    config_entries = output_dir / "apk_download_config_entries.tsv"

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence(
        evidence_rows,
        path=gamelogin_diffable,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="01-bridge-signature",
        markers=(
            "public bool iszip; //Field offset: 0x10",
            "public int callbackid; //Field offset: 0x14",
            "public static void F_GetServerList(int callbackid, string jsonData, bool iszip)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=setting_constant,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="02-setting-enum",
        markers=("ServerListUrl = 8,",),
    )
    _append_matching_evidence(
        evidence_rows,
        path=config_entries,
        export_base=export_base,
        kind="config",
        stage="03-serverlist-url",
        markers=(
            "setting.config\tServerListUrl\thttps://prod-login-frxxz.akbing.com/game/server",
            "assets/bin/Data/bea4740b1585fe342a3e946e11fd04d3\tServerListUrl\thttps://frxxz-test1.eyugame.com/xiuxian-platform/game/server",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=gamelogin_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="04-request-body",
        section_marker=(
            "Method: System.Void F_GetServerList(System.Int32 callbackid, "
            "System.String jsonData, System.Boolean iszip)"
        ),
        markers=(
            "Method: System.Void F_GetServerList(System.Int32 callbackid, System.String jsonData, System.Boolean iszip)",
            "Move [X19+20], W22",
            "Move [X19+16], W8",
            "Or W1, W31, 8",
            "Call GameInitSettingModel.F_GetSettingValue, X0, X1",
            "Call String.Format, X0, X1, X2, X3",
            "Call Debuger.UploadLog, X0",
            "Move W1, 1200",
            "Call PhoneHelper.F_UploadThinkingLaunchProcess, X0, X1, X2",
            "Call UnityWebRequestDelegate..ctor, X0, X1, X2",
            "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3",
            "Call CoroutineManager.StartCoroutine, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=gamelogin_nested_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="05-success-callback",
        section_marker="Method: System.Void <F_GetServerList>b__0(UnityEngine.Networking.DownloadHandler www)",
        markers=(
            "Method: System.Void <F_GetServerList>b__0(UnityEngine.Networking.DownloadHandler www)",
            "Call DownloadHandler.get_data, X0",
            "Call UtilCompress.DecompressFromGzip, X0",
            "Call Encoding.get_UTF8",
            "Move W1, 1201",
            "Call DownloadHandler.get_text, X0",
            "Call CallBackManager.CallStringDelegate, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=gamelogin_nested_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="06-error-callback",
        section_marker="Method: System.Void <F_GetServerList>b__1(System.String error)",
        markers=(
            "Method: System.Void <F_GetServerList>b__1(System.String error)",
            "Call String.IsNullOrEmpty, X0",
            "Call String.Concat, X0, X1",
            "Call Debuger.LogError, X0, X1",
            "Move W1, 1202",
            "Call PhoneHelper.F_UploadThinkingLaunchProcess, X0, X1, X2",
            "Call CallBackManager.CallStringDelegate, X0, X1",
        ),
    )

    checks = {
        "gamelogin_diffable_cs": gamelogin_diffable.is_file(),
        "gamelogin_isil": gamelogin_isil.is_file(),
        "gamelogin_callback_isil": gamelogin_nested_isil.is_file(),
        "serverlist_setting_enum_8": _evidence_has_stage(evidence_rows, "02-setting-enum", "ServerListUrl = 8,"),
        "prod_serverlist_url_config": _evidence_has_stage(
            evidence_rows,
            "03-serverlist-url",
            "setting.config\tServerListUrl\thttps://prod-login-frxxz.akbing.com/game/server",
        ),
        "loads_setting_value_8": _evidence_has_stage(evidence_rows, "04-request-body", "Or W1, W31, 8")
        and _evidence_has_stage(
            evidence_rows,
            "04-request-body",
            "Call GameInitSettingModel.F_GetSettingValue, X0, X1",
        ),
        "formats_request": _evidence_has_stage(
            evidence_rows,
            "04-request-body",
            "Call String.Format, X0, X1, X2, X3",
        ),
        "posts_via_fileutil": _evidence_has_stage(
            evidence_rows,
            "04-request-body",
            "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3",
        ),
        "starts_coroutine": _evidence_has_stage(
            evidence_rows,
            "04-request-body",
            "Call CoroutineManager.StartCoroutine, X0",
        ),
        "success_gzip_decode": _evidence_has_stage(
            evidence_rows,
            "05-success-callback",
            "Call DownloadHandler.get_data, X0",
        )
        and _evidence_has_stage(
            evidence_rows,
            "05-success-callback",
            "Call UtilCompress.DecompressFromGzip, X0",
        )
        and _evidence_has_stage(evidence_rows, "05-success-callback", "Call Encoding.get_UTF8"),
        "success_plain_text": _evidence_has_stage(
            evidence_rows,
            "05-success-callback",
            "Call DownloadHandler.get_text, X0",
        ),
        "success_returns_to_lua": _evidence_has_stage(
            evidence_rows,
            "05-success-callback",
            "Call CallBackManager.CallStringDelegate, X0, X1",
        ),
        "error_reports_and_returns_to_lua": _evidence_has_stage(
            evidence_rows,
            "06-error-callback",
            "Move W1, 1202",
        )
        and _evidence_has_stage(
            evidence_rows,
            "06-error-callback",
            "Call CallBackManager.CallStringDelegate, X0, X1",
        ),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "cpp2il_gamelogin_serverlist_bridge_evidence.tsv", fields, evidence_rows)
    _write_cpp2il_gamelogin_serverlist_bridge_markdown(
        output_dir / "cpp2il_gamelogin_serverlist_bridge_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "cpp2il_gamelogin_serverlist_bridge_report.md"),
        "evidence_path": str(output_dir / "cpp2il_gamelogin_serverlist_bridge_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "gamelogin_diffable_cs": str(gamelogin_diffable),
            "gamelogin_isil": str(gamelogin_isil),
            "gamelogin_callback_isil": str(gamelogin_nested_isil),
            "setting_constant": str(setting_constant),
            "config_entries": str(config_entries),
        },
    }
    (output_dir / "cpp2il_gamelogin_serverlist_bridge_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_cpp2il_fileutil_post_loader_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Cpp2IL FileUtil post loader report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- `FileUtil.F_LoadFilePost(url, postData, finishFunc, error)` returns an IEnumerator state machine and is used by `GameLoginBridge.F_GetServerList` to issue the server-list request.",
        "- The state machine encodes `postData` with UTF-8, creates a `UnityWebRequest`, attaches `UploadHandlerRaw`, `CertificateHandler`, and `DownloadHandlerBuffer`, sets one request header, then calls `SendWebRequest`.",
        "- On success it retrieves `UnityWebRequest.downloadHandler` and invokes the supplied `UnityWebRequestDelegate`.",
        "- On error or timeout it reads `UnityWebRequest.error`, invokes the supplied error callback, disposes the request, and has a retry path that starts `FileUtil.F_LoadFilePost` again through `CoroutineManager.StartCoroutine`.",
        "- Cpp2IL ISIL does not resolve the exact request-header name/value at this layer. The presence of `F_LoadFilePost`, `UnityWebRequest..ctor`, and `UploadHandlerRaw` is enough to classify this as a raw POST-style UnityWebRequest path, while exact header strings should remain an unresolved evidence gap unless string-usage mapping is added later.",
        "",
        "Reconstructed loader shape:",
        "",
        "```text",
        "F_LoadFilePost(url, postData, finishFunc, error)",
        "  -> new <F_LoadFilePost>d__7 { postData, url, finishFunc, error }",
        "  -> Encoding.UTF8.GetBytes(postData)",
        "  -> new UnityWebRequest(url, method)",
        "  -> new UploadHandlerRaw(bytes) -> request.uploadHandler",
        "  -> new CertificateHandler() -> request.certificateHandler",
        "  -> request.SetRequestHeader(header, value)",
        "  -> new DownloadHandlerBuffer() -> request.downloadHandler",
        "  -> request.SendWebRequest()",
        "  -> wait until isDone or timeout > 8s",
        "  -> success: finishFunc.Invoke(request.downloadHandler)",
        "  -> error/timeout: error.Invoke(request.error), request.Dispose(), optional retry",
        "```",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'cpp2il_fileutil_post_loader_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'cpp2il_fileutil_post_loader_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_cpp2il_fileutil_post_loader_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Recover the UnityWebRequest loader used by GameLoginBridge POST requests."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    fileutil_diffable = output_dir / _CPP2IL_FILEUTIL_DIFFABLE
    fileutil_isil = output_dir / _CPP2IL_FILEUTIL_ISIL
    post_state_isil = output_dir / _CPP2IL_FILEUTIL_POST_ISIL

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence_in_section(
        evidence_rows,
        path=fileutil_diffable,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="01-state-fields",
        section_marker="private sealed class <F_LoadFilePost>d__7 : IEnumerator<Object>, IEnumerator, IDisposable",
        end_prefixes=(),
        end_markers=("private sealed class <F_LoadFileTemp>d__6 : IEnumerator<Object>, IEnumerator, IDisposable",),
        markers=(
            "public string postData; //Field offset: 0x20",
            "public string url; //Field offset: 0x28",
            "public UnityWebRequestDelegate finishFunc; //Field offset: 0x30",
            "public Action<String> error; //Field offset: 0x38",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=fileutil_diffable,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="02-fileutil-static",
        markers=(
            "private const int timeout = 8; //Field offset: 0x0",
            "private static Dictionary<String, Int32> tryNumDic; //Field offset: 0x8",
            "public static IEnumerator F_LoadFilePost(string url, string postData, UnityWebRequestDelegate finishFunc, Action<String> error = null)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=fileutil_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="02-factory",
        section_marker=(
            "Method: System.Collections.IEnumerator F_LoadFilePost(System.String url, System.String postData, "
            "UnityWebRequestDelegate finishFunc, System.Action`1<System.String> error = null)"
        ),
        markers=(
            "Method: System.Collections.IEnumerator F_LoadFilePost(System.String url, System.String postData, UnityWebRequestDelegate finishFunc, System.Action`1<System.String> error = null)",
            "Move [X23+32], X21",
            "Move [X23+40], X22",
            "Move [X23+48], X20",
            "Move [X23+56], X19",
            "Return X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=post_state_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="03-request-construct",
        section_marker="Method: System.Boolean MoveNext()",
        markers=(
            "Call Encoding.get_UTF8",
            "Call UnityWebRequest..ctor, X0, X1, X2",
            "Call UploadHandlerRaw..ctor, X0, X1",
            "Call UnityWebRequest.set_uploadHandler, X0, X1",
            "Call CertificateHandler..ctor, X0",
            "Call UnityWebRequest.set_certificateHandler, X0, X1",
            "Call UnityWebRequest.SetRequestHeader, X0, X1, X2",
            "Call DownloadHandlerBuffer..ctor, X0",
            "Call UnityWebRequest.set_downloadHandler, X0, X1",
            "Call UnityWebRequest.SendWebRequest, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=post_state_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="04-response-success",
        section_marker="Method: System.Boolean MoveNext()",
        markers=(
            "Call UnityWebRequest.get_isDone, X0",
            "Call UnityWebRequest.get_error, X0",
            "Call String.IsNullOrEmpty, X0",
            "Call UnityWebRequest.get_downloadHandler, X0",
            "Call UnityWebRequestDelegate.Invoke, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=post_state_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="05-timeout-error-retry",
        section_marker="Method: System.Boolean MoveNext()",
        markers=(
            "Move S0, 8",
            "Call Time.get_deltaTime",
            "Call UnityWebRequest.get_error, X0",
            "Call UnityWebRequest.Dispose, X0",
            "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3",
            "Call CoroutineManager.StartCoroutine, X0",
        ),
    )

    checks = {
        "fileutil_diffable_cs": fileutil_diffable.is_file(),
        "fileutil_isil": fileutil_isil.is_file(),
        "post_state_machine_isil": post_state_isil.is_file(),
        "captures_url_postdata_callbacks": _evidence_has_stage(evidence_rows, "01-state-fields", "public string postData; //Field offset: 0x20")
        and _evidence_has_stage(evidence_rows, "01-state-fields", "public string url; //Field offset: 0x28")
        and _evidence_has_stage(
            evidence_rows,
            "01-state-fields",
            "public UnityWebRequestDelegate finishFunc; //Field offset: 0x30",
        ),
        "encodes_postdata_utf8": _evidence_has_stage(evidence_rows, "03-request-construct", "Call Encoding.get_UTF8"),
        "uses_unitywebrequest": _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call UnityWebRequest..ctor, X0, X1, X2",
        ),
        "uses_raw_upload_body": _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call UploadHandlerRaw..ctor, X0, X1",
        )
        and _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call UnityWebRequest.set_uploadHandler, X0, X1",
        ),
        "sets_header_and_download_buffer": _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call UnityWebRequest.SetRequestHeader, X0, X1, X2",
        )
        and _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call DownloadHandlerBuffer..ctor, X0",
        ),
        "sends_and_invokes_success_delegate": _evidence_has_stage(
            evidence_rows,
            "03-request-construct",
            "Call UnityWebRequest.SendWebRequest, X0",
        )
        and _evidence_has_stage(
            evidence_rows,
            "04-response-success",
            "Call UnityWebRequestDelegate.Invoke, X0, X1",
        ),
        "handles_timeout_error_retry": _evidence_has_stage(evidence_rows, "05-timeout-error-retry", "Move S0, 8")
        and _evidence_has_stage(evidence_rows, "05-timeout-error-retry", "Call UnityWebRequest.Dispose, X0")
        and _evidence_has_stage(
            evidence_rows,
            "05-timeout-error-retry",
            "Call FileUtil.F_LoadFilePost, X0, X1, X2, X3",
        ),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "cpp2il_fileutil_post_loader_evidence.tsv", fields, evidence_rows)
    _write_cpp2il_fileutil_post_loader_markdown(
        output_dir / "cpp2il_fileutil_post_loader_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "cpp2il_fileutil_post_loader_report.md"),
        "evidence_path": str(output_dir / "cpp2il_fileutil_post_loader_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "fileutil_diffable_cs": str(fileutil_diffable),
            "fileutil_isil": str(fileutil_isil),
            "post_state_machine_isil": str(post_state_isil),
        },
    }
    (output_dir / "cpp2il_fileutil_post_loader_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_lua_serverlist_response_flow_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Lua server-list response flow report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report covers the Lua-side handoff after `GameLoginBridge.F_GetServerList` returns the HTTP response string to Lua.",
        "- `GetServerInfo:GetSDKServerList` registers a string callback, raises `GET_SERVER_LIST_SUCCEED(jsonData)`, and `ServerGroupListView` listens to that event.",
        "- `LoginModel:SetServerListData` decodes the JSON into `LoginData`; `LoginData:ServerVoInfo` wraps `servers[]` as `LoginServer`, where `data.host/data.port` become `V_Host/V_Port`.",
        "- The selected server is passed into `LoginMgr:IntoGame`, cached by `SetServerData`, and finally reaches `SocketBridge.F_Connect(pIp, pPort, ...)`.",
        "",
        "Reconstructed chain:",
        "",
        "```text",
        "GameLoginBridge.F_GetServerList(callbackId, jsonData, isZip)",
        "  -> LuaCallBackMgr string callback(jsonData)",
        "  -> LuaEventMgr.RaiseEvent(GET_SERVER_LIST_SUCCEED, jsonData)",
        "  -> ServerGroupListView.F_ServerListUpdateFun(jsonStr)",
        "  -> LoginModel.SetServerListData(jsonStr)",
        "  -> LuaUtil.decode(jsonStr, typeof(LoginData))",
        "  -> LoginData.FillData(data).ServerInfo(data.servers)",
        "  -> LoginData.ServerVoInfo: LoginServer.FillData(server)",
        "  -> LoginServer.V_Host = data.host / V_Port = data.port",
        "  -> WinLogin or AutoIntoGame passes V_Host/V_Port into LoginMgr.IntoGame",
        "  -> EnterGameInfo.StartEnter_1 -> SetServerData + SocketConnect",
        "  -> SocketManager.F_InitSocketCon -> LuaSocket.F_Connect",
        "  -> SocketBridge.F_Connect(pIp, pPort, pIslogin, isMainSocket)",
        "```",
        "",
        "Response schema inferred from Lua code: top-level responses may be wrapped as `{code, data}`, and the effective payload uses `data.servers[]` with `host`, `port`, `id`, `server`, `name`, `group`, and related display fields. The local fallback emits the same `data.servers` shape.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'lua_serverlist_response_flow_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'lua_serverlist_response_flow_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_lua_serverlist_response_flow_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Trace the Lua server-list response from callback event to socket target fields."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    common_event_lua = _find_lua_text_asset(export_base, "CommonEventType.lua", ("GET_SERVER_LIST_SUCCEED",))
    get_server_info_lua = _find_lua_text_asset(
        export_base,
        "GetServerInfo.lua",
        ("GetSDKServerList", "AddCallBackStringDelegate", "F_GetServerList"),
    )
    server_group_view_lua = _find_lua_text_asset(
        export_base,
        "ServerGroupListView.lua",
        ("GET_SERVER_LIST_SUCCEED", "SetServerListData"),
    )
    login_model_lua = _find_lua_text_asset(
        export_base,
        "LoginModel.lua",
        ("SetServerListData", "LuaUtil.decode(jsonStr,typeof(LoginData))", "SetServerData"),
    )
    login_data_lua = _find_lua_text_asset(
        export_base,
        "LoginData.lua",
        ("self:ServerInfo(data.servers)", "ServerVoInfo", "LoginServer"),
    )
    login_server_lua = _find_lua_text_asset(
        export_base,
        "LoginServer.lua",
        ("self.V_Host=data.host", "self.V_Port=data.port"),
    )
    win_login_lua = _find_lua_text_asset(
        export_base,
        "WinLogin.lua",
        ("self.V_SelectIP=self.V_AccountServerData.V_Host", "LoginMgr.Inst_get():IntoGame"),
    )
    login_mgr_lua = _find_lua_text_asset(
        export_base,
        "LoginMgr.lua",
        ("function _M.IntoGame", "sd.V_Host", "self.EnterGameInfo:StartEnter_1"),
    )
    enter_game_info_lua = _find_lua_text_asset(
        export_base,
        "EnterGameInfo.lua",
        ("function _M.StartEnter_1", "SetServerData", "SocketConnect"),
    )
    socket_manager_lua = _find_lua_text_asset(
        export_base,
        "SocketManager.lua",
        ("function _M.F_InitSocketCon", "so:F_Connect(pServer,pPort,pIslogin)"),
    )
    lua_socket_lua = _find_lua_text_asset(
        export_base,
        "LuaSocket.lua",
        ("function _M.F_Connect", "SocketBridge.F_Connect(pIp,pPort or 0,pIslogin,self.isMainSocket)"),
    )

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence(
        evidence_rows,
        path=common_event_lua,
        export_base=export_base,
        kind="lua",
        stage="00-event-constant",
        markers=('_M.GET_SERVER_LIST_SUCCEED="GET_SERVER_LIST_SUCCEED"',),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=get_server_info_lua,
        export_base=export_base,
        kind="lua",
        stage="01-http-callback",
        section_marker="function _M.GetSDKServerList(self,Pid,LoginToken)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.GetSDKServerList(self,Pid,LoginToken)",
            "local serverListCallbackId",
            "local callback=function(jsonData)",
            "LuaCallBackMgr.RemoveCallBackStringDelegate(serverListCallbackId)",
            "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.GET_SERVER_LIST_SUCCEED,jsonData)",
            "serverListCallbackId=LuaCallBackMgr.AddCallBackStringDelegate(callback)",
            "GameLoginBridge.F_GetServerList(serverListCallbackId,jsonStr,isZip)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=get_server_info_lua,
        export_base=export_base,
        kind="lua",
        stage="02-local-fallback",
        section_marker="function _M.GetLocalServerList(self)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.GetLocalServerList(self)",
            "local jsonData={code=0,data={roles={},servers={},groups={}}}",
            "table.insert(jsonData.data.servers,self:FormatServerInfo(serverInfo))",
            "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.GET_SERVER_LIST_SUCCEED,jsonStr)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=server_group_view_lua,
        export_base=export_base,
        kind="lua",
        stage="03-event-listener",
        markers=(
            "self.F_ServerListUpdateFun=function(jsonStr)",
            "self:ServerListData(jsonStr)",
            "LuaEventMgr.Inst_get():AddEventHandler(CommonEventType.GET_SERVER_LIST_SUCCEED,self.F_ServerListUpdateFun)",
            "LoginMgr.Inst_get().LoginModel:SetServerListData(jsonStr)",
            "self.V_Data=LoginMgr.Inst_get().LoginModel:GetServerData()",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_model_lua,
        export_base=export_base,
        kind="lua",
        stage="04-json-decode",
        section_marker="function _M.SetServerListData(self,jsonStr)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.SetServerListData(self,jsonStr)",
            'local LoginData=require"GameSystem.Game.Message.module.user.login.packet.vo.LoginData"',
            "self.V_LoginData=LuaUtil.decode(jsonStr,typeof(LoginData))",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_data_lua,
        export_base=export_base,
        kind="lua",
        stage="05-login-data-schema",
        section_marker="function _M.FillData(self,data)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.FillData(self,data)",
            "if data and data.code and data.data then",
            "data=data.data",
            "self:ServerInfo(data.servers)",
            "self:RoleInfo(data.roles)",
            "self:GroupsInfo(data.groups)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_data_lua,
        export_base=export_base,
        kind="lua",
        stage="06-server-list-wrap",
        section_marker="function _M.ServerVoInfo(self,list)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.ServerVoInfo(self,list)",
            'local LoginServer=require"GameSystem.Game.Message.module.user.login.packet.vo.LoginServer"',
            "local loginGroup=LoginServer.new()",
            "loginGroup:FillData(data)",
            "self.V_ServerDic:LuaDic_AddOrSetItem(id,loginGroup)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_server_lua,
        export_base=export_base,
        kind="lua",
        stage="07-host-port-map",
        section_marker="function _M.FillData(self,data)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "self.V_Name=data.name",
            "self.V_Id=data.id",
            "self.V_Server=data.server",
            "self.V_Host=data.host",
            "self.V_Port=data.port",
            "self.V_Group=data.group",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=win_login_lua,
        export_base=export_base,
        kind="lua",
        stage="08-selected-server-ui",
        section_marker="function _M.IntoGame(self,userName)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "local loginAccount=LoginMgr.Inst_get().LoginModel:GetLoginAccountData()",
            "LoginMgr.Inst_get():IntoGame(userName,self.V_AccountServerData.V_Server or 1,self.V_SelectIP,self.V_SelectPort,self.V_SelectName,loginAccount.V_PId or\"\",self.V_AccountServerData.V_Id,self.V_SuperToken,self.V_ChannelPackage)",
            "LoginMgr.Inst_get():IntoGame(userName,self.V_Id or 1,self.V_SelectIP,self.V_SelectPort,self.V_SelectName,self.V_PId or\"\",self.V_AccountServerData.V_Id,self.V_SuperToken,self.V_ChannelPackage)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua",
        stage="09-auto-or-manager-enter",
        section_marker="function _M.AutoIntoGameInWebGL(self)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "local sd=loginAccount.V_ServerData",
            "self:IntoGame(userName,serverId,sd.V_Host,sd.V_Port,sd.V_Name,loginAccount.V_PId or\"\",sd.V_Id,nil,channelPackage)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_mgr_lua,
        export_base=export_base,
        kind="lua",
        stage="10-loginmgr-enter",
        section_marker="function _M.IntoGame(self,userName,serverId,serverIp,serverPort,serverName,pid,id,superToken,channelPackage)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.IntoGame(self,userName,serverId,serverIp,serverPort,serverName,pid,id,superToken,channelPackage)",
            "self.EnterGameInfo:StartEnter_1(userName,serverId,serverIp,serverPort,serverName,pid,id,superToken,channelPackage)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=login_model_lua,
        export_base=export_base,
        kind="lua",
        stage="11-cache-server-data",
        section_marker="function _M.SetServerData(self,userName,serverId,serverIp,serverPort,serverName,pid,id,channelPackage)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "serverItem.domain=serverIp",
            "serverItem.port=serverPort",
            "self.V_CurServerItem=serverItem",
            "LuaGameSettingBridge.SaveEnterServerSetting(userName,serverIp,serverPort,serverName,serverId,id,channelPackage)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=enter_game_info_lua,
        export_base=export_base,
        kind="lua",
        stage="12-start-enter",
        section_marker="function _M.StartEnter_1(self,userName,serverId,serverIp,serverPort,serverName,pid,id,superToken,channelPackage)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "LoginMgr.Inst_get():SetServerData(userName,serverId,serverIp,serverPort,serverName,pid,id,self.V_ChannelPackage)",
            "self:SocketConnect()",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=socket_manager_lua,
        export_base=export_base,
        kind="lua",
        stage="13-socket-manager",
        section_marker="function _M.F_InitSocketCon(self,pServer,pPort,pIslogin,isMainSocket)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            "function _M.F_InitSocketCon(self,pServer,pPort,pIslogin,isMainSocket)",
            "local so=self:GetSocket(isMainSocket)",
            "so:F_Connect(pServer,pPort,pIslogin)",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=lua_socket_lua,
        export_base=export_base,
        kind="lua",
        stage="14-native-socket-bridge",
        section_marker="function _M.F_Connect(self,pIp,pPort,pIslogin)",
        end_prefixes=(),
        end_markers=("function _M.",),
        markers=(
            'local SocketBridge=require"LuaBridge.EngineBridge.SocketBridge"',
            "SocketBridge.F_Connect(pIp,pPort or 0,pIslogin,self.isMainSocket)",
        ),
    )

    checks = {
        "common_event_constant": _evidence_has_stage(
            evidence_rows,
            "00-event-constant",
            '_M.GET_SERVER_LIST_SUCCEED="GET_SERVER_LIST_SUCCEED"',
        ),
        "http_callback_registers_lua_delegate": _evidence_has_stage(
            evidence_rows,
            "01-http-callback",
            "serverListCallbackId=LuaCallBackMgr.AddCallBackStringDelegate(callback)",
        )
        and _evidence_has_stage(
            evidence_rows,
            "01-http-callback",
            "GameLoginBridge.F_GetServerList(serverListCallbackId,jsonStr,isZip)",
        ),
        "http_callback_raises_response_event": _evidence_has_stage(
            evidence_rows,
            "01-http-callback",
            "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.GET_SERVER_LIST_SUCCEED,jsonData)",
        ),
        "local_fallback_same_event_shape": _evidence_has_stage(
            evidence_rows,
            "02-local-fallback",
            "local jsonData={code=0,data={roles={},servers={},groups={}}}",
        )
        and _evidence_has_stage(
            evidence_rows,
            "02-local-fallback",
            "LuaEventMgr.Inst_get():RaiseEvent(CommonEventType.GET_SERVER_LIST_SUCCEED,jsonStr)",
        ),
        "event_listener_updates_login_model": _evidence_has_stage(
            evidence_rows,
            "03-event-listener",
            "LoginMgr.Inst_get().LoginModel:SetServerListData(jsonStr)",
        ),
        "login_model_decodes_login_data": _evidence_has_stage(
            evidence_rows,
            "04-json-decode",
            "self.V_LoginData=LuaUtil.decode(jsonStr,typeof(LoginData))",
        ),
        "login_data_unwraps_servers": _evidence_has_stage(
            evidence_rows,
            "05-login-data-schema",
            "self:ServerInfo(data.servers)",
        ),
        "server_list_wraps_login_server": _evidence_has_stage(
            evidence_rows,
            "06-server-list-wrap",
            "loginGroup:FillData(data)",
        ),
        "login_server_maps_host_port": _evidence_has_stage(
            evidence_rows,
            "07-host-port-map",
            "self.V_Host=data.host",
        )
        and _evidence_has_stage(evidence_rows, "07-host-port-map", "self.V_Port=data.port"),
        "selected_server_enters_game": _evidence_has_stage(
            evidence_rows,
            "08-selected-server-ui",
            "LoginMgr.Inst_get():IntoGame(userName,self.V_AccountServerData.V_Server or 1,self.V_SelectIP,self.V_SelectPort,self.V_SelectName,loginAccount.V_PId or\"\",self.V_AccountServerData.V_Id,self.V_SuperToken,self.V_ChannelPackage)",
        )
        or _evidence_has_stage(
            evidence_rows,
            "09-auto-or-manager-enter",
            "self:IntoGame(userName,serverId,sd.V_Host,sd.V_Port,sd.V_Name,loginAccount.V_PId or\"\",sd.V_Id,nil,channelPackage)",
        ),
        "loginmgr_forwards_to_enter_game": _evidence_has_stage(
            evidence_rows,
            "10-loginmgr-enter",
            "self.EnterGameInfo:StartEnter_1(userName,serverId,serverIp,serverPort,serverName,pid,id,superToken,channelPackage)",
        ),
        "server_data_caches_domain_port": _evidence_has_stage(
            evidence_rows,
            "11-cache-server-data",
            "serverItem.domain=serverIp",
        )
        and _evidence_has_stage(evidence_rows, "11-cache-server-data", "serverItem.port=serverPort"),
        "start_enter_connects_socket": _evidence_has_stage(
            evidence_rows,
            "12-start-enter",
            "self:SocketConnect()",
        ),
        "socket_manager_forwards_target": _evidence_has_stage(
            evidence_rows,
            "13-socket-manager",
            "so:F_Connect(pServer,pPort,pIslogin)",
        ),
        "lua_socket_calls_native_bridge": _evidence_has_stage(
            evidence_rows,
            "14-native-socket-bridge",
            "SocketBridge.F_Connect(pIp,pPort or 0,pIslogin,self.isMainSocket)",
        ),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "lua_serverlist_response_flow_evidence.tsv", fields, evidence_rows)
    _write_lua_serverlist_response_flow_markdown(
        output_dir / "lua_serverlist_response_flow_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "lua_serverlist_response_flow_report.md"),
        "evidence_path": str(output_dir / "lua_serverlist_response_flow_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "common_event_lua": str(common_event_lua) if common_event_lua else "",
            "get_server_info_lua": str(get_server_info_lua) if get_server_info_lua else "",
            "server_group_view_lua": str(server_group_view_lua) if server_group_view_lua else "",
            "login_model_lua": str(login_model_lua) if login_model_lua else "",
            "login_data_lua": str(login_data_lua) if login_data_lua else "",
            "login_server_lua": str(login_server_lua) if login_server_lua else "",
            "win_login_lua": str(win_login_lua) if win_login_lua else "",
            "login_mgr_lua": str(login_mgr_lua) if login_mgr_lua else "",
            "enter_game_info_lua": str(enter_game_info_lua) if enter_game_info_lua else "",
            "socket_manager_lua": str(socket_manager_lua) if socket_manager_lua else "",
            "lua_socket_lua": str(lua_socket_lua) if lua_socket_lua else "",
        },
    }
    (output_dir / "lua_serverlist_response_flow_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


_CPP2IL_SOCKETBRIDGE_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/LuaBridge/EngineBridge/SocketBridge.txt"
)
_CPP2IL_PROTOBRIDGE_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/LuaBridge/Utils/ProtoBridge.txt"
)
_CPP2IL_SOCKETMANAGER_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Net/Sockets/SocketManager.txt"
)
_CPP2IL_BYTESOCKET_DIFFABLE = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/Core/Net/Sockets/ByteSocket.cs"
)
_CPP2IL_BYTESOCKET_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Net/Sockets/ByteSocket.txt"
)
_CPP2IL_LUSUOSTREAMQUICK_DIFFABLE = Path(
    "cpp2il_2022_1_pre21_arm64_diffable_cs/DiffableCs/Assembly-CSharp/Core/Net/LusuoStreamQuick.cs"
)
_CPP2IL_LUSUOSTREAMQUICK_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Net/LusuoStreamQuick.txt"
)
_CPP2IL_CSMESSAGEPOOL_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Net/CSMessagePool.txt"
)
_CPP2IL_POOLMESSAGEMANAGE_ISIL = Path(
    "cpp2il_2022_1_pre21_arm64_isil/IsilDump/Assembly-CSharp/Core/Net/PoolMessageManage.txt"
)


def _write_cpp2il_socket_proto_bridge_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Cpp2IL socket/proto bridge report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report follows the Lua login packet send path beyond `SocketBridge.F_Send` into the recovered IL2CPP socket/proto wrappers.",
        "- `ProtoBridge.WriteInt` and `ProtoBridge.WriteBigString` dispatch into the shared `LusuoStreamQuick` write buffer.",
        "- `SocketBridge.F_Send(proId, isMainSocket, sn)` dispatches to `SocketManager.F_Send`, which selects a `ByteSocket` and calls `ByteSocket.F_Send(proId, sn)`.",
        "- `ByteSocket.F_Send` builds a packet stream by writing `sn` and `proId`, then writes a non-compressed length word for `body_length + 12`, appends the header stream and body stream, and finally sends the buffer through `Socket.BeginSend`.",
        "- `WriteBigString` is length-prefixed UTF-8 bytes: empty string writes length `0`; non-empty string gets `Encoding.UTF8` bytes, writes byte length with `WriteInt`, then writes the bytes.",
        "- `WriteInt` is governed by `LusuoStreamQuick.isCompress`; Cpp2IL shows the compressed integer routine uses varint/zig-zag style operations. This is serialization compression, not evidence of cryptographic encryption.",
        "",
        "Reconstructed send chain:",
        "",
        "```text",
        "Lua CM_Login.writing()",
        "  -> ProtoBridge.Write* / CSLusuoStreamWarp.Write*",
        "  -> LusuoStreamQuick shared write buffer",
        "  -> SocketBridge.F_Send(proId, isMainSocket, sn)",
        "  -> SocketManager.F_Send(proId, isMainSocket, sn)",
        "  -> ByteSocket.F_Send(proId, sn)",
        "  -> header stream: WriteInt(sn), WriteInt(proId)",
        "  -> packet stream: WriteNoCompress(body_length + 12), header stream, body stream",
        "  -> Socket.BeginSend(packet_buffer, 0, packet_length, ...)",
        "```",
        "",
        "Working packet-frame hypothesis from method evidence:",
        "",
        "```text",
        "int32/no-compress total_length = body_length + 12",
        "int/possibly-compress sn",
        "int/possibly-compress proId",
        "body bytes already written by ProtoBridge/LusuoStreamQuick",
        "```",
        "",
        "The `+12` should be treated as a current hypothesis, not a final wire spec, until live captures or a receive-side parser confirm how length and compressed-int widths interact on real packets.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'cpp2il_socket_proto_bridge_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'cpp2il_socket_proto_bridge_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_cpp2il_socket_proto_bridge_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Trace the recovered IL2CPP socket/proto send wrappers and packet frame evidence."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    socketbridge_isil = output_dir / _CPP2IL_SOCKETBRIDGE_ISIL
    protobridge_isil = output_dir / _CPP2IL_PROTOBRIDGE_ISIL
    socketmanager_isil = output_dir / _CPP2IL_SOCKETMANAGER_ISIL
    bytesocket_diffable = output_dir / _CPP2IL_BYTESOCKET_DIFFABLE
    bytesocket_isil = output_dir / _CPP2IL_BYTESOCKET_ISIL
    stream_diffable = output_dir / _CPP2IL_LUSUOSTREAMQUICK_DIFFABLE
    stream_isil = output_dir / _CPP2IL_LUSUOSTREAMQUICK_ISIL

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence_in_section(
        evidence_rows,
        path=protobridge_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="01-protobridge-int",
        section_marker="Method: System.Void WriteInt(System.Int32 data)",
        markers=(
            "Method: System.Void WriteInt(System.Int32 data)",
            "Call LusuoStreamQuick.WriteInt, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=protobridge_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="02-protobridge-string",
        section_marker="Method: System.Void WriteBigString(System.String strOut)",
        markers=(
            "Method: System.Void WriteBigString(System.String strOut)",
            "Call LusuoStreamQuick.WriteBigString, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_diffable,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="03-stream-fields",
        section_marker="public class LusuoStreamQuick",
        end_prefixes=(),
        end_markers=("public int Length",),
        markers=(
            "private Byte[] mTempByteArray; //Field offset: 0x18",
            "private int mCurrentLength; //Field offset: 0x20",
            "private int mCurrentPosition; //Field offset: 0x24",
            "public bool isCompress; //Field offset: 0x38",
            "public bool IsLittleEndian; //Field offset: 0x39",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="04-stream-write-int",
        section_marker="Method: System.Void WriteInt(System.Int32 Num)",
        markers=(
            "Method: System.Void WriteInt(System.Int32 Num)",
            "Call LusuoStreamQuick.WriteUInt, X0, X1",
            "Method: System.Void WriteIntCompress(System.Int32 value)",
        ),
        end_prefixes=(),
        end_markers=("Method: System.Void WriteBigString",),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="05-stream-write-int-compress",
        section_marker="Method: System.Void WriteIntCompress(System.Int32 value)",
        markers=(
            "Method: System.Void WriteIntCompress(System.Int32 value)",
            "Xor X9, X11, X9",
            "Or W11, W9, 128",
            "ShiftRight X9, 7",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="06-stream-write-string",
        section_marker="Method: System.Void WriteBigString(System.String strOut)",
        markers=(
            "Method: System.Void WriteBigString(System.String strOut)",
            "Call String.IsNullOrEmpty, X0",
            "Call Encoding.get_UTF8",
            "Call LusuoStreamQuick.WriteInt, X0, X1",
            "Call LusuoStreamQuick.WriteBytes, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="07-stream-no-compress",
        section_marker="Method: System.Void WriteNoCompress(System.Int32 num)",
        markers=(
            "Method: System.Void WriteNoCompress(System.Int32 num)",
            "Call LusuoStreamQuick.WriteUInt, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=stream_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="08-stream-buffer",
        section_marker="Method: System.Byte[] GetBuffer()",
        markers=(
            "Method: System.Byte[] GetBuffer()",
            "Move X0, [X0+24]",
            "Return X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=socketbridge_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="09-socketbridge-send",
        section_marker="Method: System.Boolean F_Send(System.Int32 proId, System.Boolean isMainSocket = True, System.Int32 sn = 0)",
        markers=(
            "Method: System.Boolean F_Send(System.Int32 proId, System.Boolean isMainSocket = True, System.Int32 sn = 0)",
            "Call SocketManager.GetInstance",
            "Call SocketManager.F_Send, X0, X1, X2, X3",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=socketmanager_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="10-socketmanager-send",
        section_marker="Method: System.Boolean F_Send(System.Int32 proId, System.Boolean isMainSocket = True, System.Int32 sn = 0)",
        markers=(
            "Method: System.Boolean F_Send(System.Int32 proId, System.Boolean isMainSocket = True, System.Int32 sn = 0)",
            "Call ByteSocket.F_Send, X0, X1, X2",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=socketmanager_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="11-reset-write-buffer",
        section_marker="Method: System.Void ResetWriteProtoStreamBuffer()",
        markers=(
            "Method: System.Void ResetWriteProtoStreamBuffer()",
            "Call LusuoStreamQuick.Reset, X0, X1",
            "Move [X8+36], W31",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=bytesocket_diffable,
        export_base=export_base,
        kind="cpp2il_diffable_cs",
        stage="12-bytesocket-fields",
        section_marker="public class ByteSocket",
        end_prefixes=(),
        end_markers=("private static ByteSocket()",),
        markers=(
            "private const int RPC_HEADER_LENGTH = 4; //Field offset: 0x0",
            "public static bool isMessageCompress; //Field offset: 0x1",
            "private Socket m_socket; //Field offset: 0x10",
            "private bool m_IsMsgCompress; //Field offset: 0x58",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=bytesocket_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="13-bytesocket-frame",
        section_marker="Method: System.Boolean F_Send(System.Int32 proId, System.Int32 sn)",
        markers=(
            "Method: System.Boolean F_Send(System.Int32 proId, System.Int32 sn)",
            "Add W25, W25, 12",
            "Call LusuoStreamQuick..ctor, X0, X1, X2, X3",
            "Call LusuoStreamQuick.WriteInt, X0, X1",
            "Add W1, W9, W8",
            "Call LusuoStreamQuick.WriteNoCompress, X0, X1",
            "Call LusuoStreamQuick.WriteStream, X0, X1",
            "Call Socket.BeginSend, X0, X1, X2, X3, X4, X5, X6",
        ),
    )

    checks = {
        "protobridge_write_int_to_stream": _evidence_has_stage(
            evidence_rows,
            "01-protobridge-int",
            "Call LusuoStreamQuick.WriteInt, X0, X1",
        ),
        "protobridge_write_string_to_stream": _evidence_has_stage(
            evidence_rows,
            "02-protobridge-string",
            "Call LusuoStreamQuick.WriteBigString, X0, X1",
        ),
        "stream_has_compress_flag": _evidence_has_stage(
            evidence_rows,
            "03-stream-fields",
            "public bool isCompress; //Field offset: 0x38",
        ),
        "write_int_has_uncompressed_path": _evidence_has_stage(
            evidence_rows,
            "04-stream-write-int",
            "Call LusuoStreamQuick.WriteUInt, X0, X1",
        ),
        "write_int_compress_has_varint_shape": _evidence_has_stage(
            evidence_rows,
            "05-stream-write-int-compress",
            "Or W11, W9, 128",
        )
        and _evidence_has_stage(evidence_rows, "05-stream-write-int-compress", "ShiftRight X9, 7"),
        "write_big_string_length_prefixed_utf8": _evidence_has_stage(
            evidence_rows,
            "06-stream-write-string",
            "Call Encoding.get_UTF8",
        )
        and _evidence_has_stage(evidence_rows, "06-stream-write-string", "Call LusuoStreamQuick.WriteInt, X0, X1")
        and _evidence_has_stage(evidence_rows, "06-stream-write-string", "Call LusuoStreamQuick.WriteBytes, X0, X1"),
        "socketbridge_forwards_to_manager": _evidence_has_stage(
            evidence_rows,
            "09-socketbridge-send",
            "Call SocketManager.F_Send, X0, X1, X2, X3",
        ),
        "socketmanager_forwards_to_bytesocket": _evidence_has_stage(
            evidence_rows,
            "10-socketmanager-send",
            "Call ByteSocket.F_Send, X0, X1, X2",
        ),
        "reset_write_buffer_resets_stream": _evidence_has_stage(
            evidence_rows,
            "11-reset-write-buffer",
            "Call LusuoStreamQuick.Reset, X0, X1",
        ),
        "bytesocket_has_socket_and_compress_state": _evidence_has_stage(
            evidence_rows,
            "12-bytesocket-fields",
            "private Socket m_socket; //Field offset: 0x10",
        )
        and _evidence_has_stage(
            evidence_rows,
            "12-bytesocket-fields",
            "private bool m_IsMsgCompress; //Field offset: 0x58",
        ),
        "bytesocket_builds_header_body_frame": _evidence_has_stage(
            evidence_rows,
            "13-bytesocket-frame",
            "Add W25, W25, 12",
        )
        and _evidence_has_stage(evidence_rows, "13-bytesocket-frame", "Call LusuoStreamQuick.WriteNoCompress, X0, X1")
        and _evidence_has_stage(evidence_rows, "13-bytesocket-frame", "Call LusuoStreamQuick.WriteStream, X0, X1"),
        "bytesocket_sends_with_begin_send": _evidence_has_stage(
            evidence_rows,
            "13-bytesocket-frame",
            "Call Socket.BeginSend, X0, X1, X2, X3, X4, X5, X6",
        ),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "cpp2il_socket_proto_bridge_evidence.tsv", fields, evidence_rows)
    _write_cpp2il_socket_proto_bridge_markdown(
        output_dir / "cpp2il_socket_proto_bridge_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "cpp2il_socket_proto_bridge_report.md"),
        "evidence_path": str(output_dir / "cpp2il_socket_proto_bridge_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "socketbridge_isil": str(socketbridge_isil),
            "protobridge_isil": str(protobridge_isil),
            "socketmanager_isil": str(socketmanager_isil),
            "bytesocket_diffable": str(bytesocket_diffable),
            "bytesocket_isil": str(bytesocket_isil),
            "lusuo_stream_quick_diffable": str(stream_diffable),
            "lusuo_stream_quick_isil": str(stream_isil),
        },
    }
    (output_dir / "cpp2il_socket_proto_bridge_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _write_cpp2il_socket_receive_dispatch_markdown(
    path: Path,
    *,
    export_base: Path,
    output_dir: Path,
    evidence_rows: list[dict[str, object]],
    checks: dict[str, bool],
) -> None:
    lines = [
        "# Cpp2IL socket receive/dispatch report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "- This report follows the receive side that pairs with the recovered `ByteSocket.F_Send` frame construction.",
        "- `ByteSocket.ProcessReciveData` reads a 4-byte head from `Socket.Receive`, decodes it through `ByteUtil.ReadInt`, receives the remaining body, then calls `ByteSocket.ReadPackage`.",
        "- `ByteSocket.ReadPackage` wraps the received buffer in `LusuoStreamQuick`, reads several packet header integers, detects Lua-handled message ids, and feeds the payload into `PoolMessageManage.read`.",
        "- `PoolMessageManage.read` resolves Lua/CS message classes through `CSMessagePool.F_GetMessage`, constructs a stream wrapper, and drives the generated packet `reading` path.",
        "- Parsed messages are then dispatched through `CSMessagePool.F_SendHandler`; Lua registration mirrors this through `MessagePool:F_Register`, `SocketBridge.RegisterLuaPro(id)`, and `MessagePool:F_SendHandler`.",
        "- Login examples confirm concrete Lua receive handlers: `SM_Login` is id `20002`, `SM_ProtoHash` is id `20014`, and `LoginNetLogic` registers both handlers.",
        "",
        "Reconstructed receive chain:",
        "",
        "```text",
        "Socket.Receive(4-byte length head)",
        "  -> ByteUtil.ReadInt(head)",
        "  -> Socket.Receive(body bytes)",
        "  -> ByteSocket.ReadPackage()",
        "  -> LusuoStreamQuick(buffer, length, littleEndian=false, isMsgCompress)",
        "  -> ReadInt(...) for packet header fields",
        "  -> PoolMessageManage.IsLuaMessage(proId)",
        "  -> PoolMessageManage.read(proId, stream)",
        "  -> CSMessagePool.F_GetMessage(proId)",
        "  -> packet.reading(...)",
        "  -> CSMessagePool.F_SendHandler(proId, message)",
        "  -> Lua MessagePool handler / LoginNetLogic callback",
        "```",
        "",
        "The exact semantic names/order of the `ReadPackage` header integers still need a tighter probe or live capture. The current static evidence is strong enough to confirm the receive-side length/body/dispatch architecture, and it cross-checks the send-side `body_length + 12` hypothesis without treating it as a final wire spec.",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in checks.items():
        lines.append(f"| `{key}` | {'ok' if value else 'missing'} |")

    lines.extend(
        [
            "",
            "## Key Evidence",
            "",
            "| Stage | Kind | Source | Line | Marker | Snippet |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in evidence_rows:
        source = str(row.get("source", "")).replace("|", "\\|")
        marker = str(row.get("marker", "")).replace("|", "\\|")
        snippet = str(row.get("snippet", "")).replace("|", "\\|")
        lines.append(
            f"| {row.get('stage', '')} | {row.get('kind', '')} | `{source}` | {row.get('line', '')} | `{marker}` | `{snippet}` |"
        )

    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Report: `{_lua_report_source(path, export_base)}`",
            f"- Evidence TSV: `{_lua_report_source(output_dir / 'cpp2il_socket_receive_dispatch_evidence.tsv', export_base)}`",
            f"- JSON: `{_lua_report_source(output_dir / 'cpp2il_socket_receive_dispatch_report.json', export_base)}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fanxiu_cpp2il_socket_receive_dispatch_probe(
    *,
    export_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Trace the recovered IL2CPP socket receive parser into Lua/CS message dispatch."""

    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    if not _is_relative_to(output_dir, export_base):
        raise FanxiuResourceError(f"导出目录必须位于导出根目录内：{export_base}")
    output_dir.mkdir(parents=True, exist_ok=True)

    bytesocket_isil = output_dir / _CPP2IL_BYTESOCKET_ISIL
    pool_message_manage_isil = output_dir / _CPP2IL_POOLMESSAGEMANAGE_ISIL
    cs_message_pool_isil = output_dir / _CPP2IL_CSMESSAGEPOOL_ISIL
    message_pool_lua = _find_lua_text_asset(
        export_base,
        "MessagePool.lua",
        ("function _M:F_Register", "SocketBridge.RegisterLuaPro", "function _M:F_SendHandler"),
    )
    socket_manager_lua = _find_lua_text_asset(
        export_base,
        "SocketManager.lua",
        ("function _M.GetMessageFromPools", "F_SendHandler"),
    )
    login_net_logic_lua = _find_lua_text_asset(
        export_base,
        "LoginNetLogic.lua",
        ("_SM_Login", "_SM_ProtoHash", "SM_ProtoHashFun"),
    )
    sm_proto_hash_lua = _find_lua_text_asset(
        export_base,
        "SM_ProtoHash.lua",
        ("function _M.reading", "return 20014"),
    )
    sm_login_lua = _find_lua_text_asset(
        export_base,
        "SM_Login.lua",
        ("function _M.reading", "return 20002"),
    )

    evidence_rows: list[dict[str, object]] = []
    _append_matching_evidence_in_section(
        evidence_rows,
        path=bytesocket_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="01-bytesocket-receive-head",
        section_marker="Method: System.Void ProcessReciveData()",
        markers=(
            "Method: System.Void ProcessReciveData()",
            "Call Socket.get_Available, X0",
            "Call Socket.Receive, X0, X1, X2, X3",
            "Call ByteUtil.ReadInt, X0, X1, X2",
            "Call Socket.Receive, X0, X1, X2, X3, X4",
            "Call ByteSocket.ReadPackage, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=bytesocket_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="02-bytesocket-readpackage-stream",
        section_marker="Method: System.Void ReadPackage()",
        markers=(
            "Method: System.Void ReadPackage()",
            "Call LusuoStreamQuick..ctor, X0, X1, X2, X3, X4",
            "Call LusuoStreamQuick.ReadInt, X0, X1",
            "Call PoolMessageManage.IsLuaMessage, X0",
            "Call List`1<Int32>.Contains, X0, X1",
            "Call PoolMessageManage.read, X0, X1",
            "Call CSMessagePool.GetInstance",
            "Call CSMessagePool.F_SendHandler, X0, X1, X2",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=pool_message_manage_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="03-poolmessage-read",
        section_marker="Method: System.Void reading(System.Int32 proId)",
        markers=(
            "Method: System.Void reading(System.Int32 proId)",
            "Call PoolMessageManage.SetData, X0",
            "Call CSMessagePool.GetInstance",
            "Call CSMessagePool.F_GetMessage, X0, X1",
            "Call CSLusuoStreamWarp..ctor, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=pool_message_manage_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="04-poolmessage-add-read",
        section_marker="Method: System.Void read(Core.Net.LusuoStreamQuick val, System.Int32 proId)",
        markers=(
            "Method: System.Void read(Core.Net.LusuoStreamQuick val, System.Int32 proId)",
            "Call PoolMessageManage.AddProId, X0",
            "Call PoolMessageManage.IsLuaMessage, X0",
            "Call PoolMessageManage.reading, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=pool_message_manage_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="05-poolmessage-lua-check",
        section_marker="Method: System.Boolean IsLuaMessage(System.Int32 proId = 0)",
        markers=(
            "Method: System.Boolean IsLuaMessage(System.Int32 proId = 0)",
            "Call CSMessagePool.GetInstance",
            "Call CSMessagePool.IsCSMessage, X0, X1",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=cs_message_pool_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="06-csmessagepool-register",
        section_marker="Method: System.Void F_Register(System.Int32 id, System.Type messageClass, Core.Net.CSNetMessageCallback handlerClass)",
        markers=(
            "Method: System.Void F_Register(System.Int32 id, System.Type messageClass, Core.Net.CSNetMessageCallback handlerClass)",
            "Call 0x20B3824, X0, X1, X2",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=cs_message_pool_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="07-csmessagepool-get-message",
        section_marker="Method: Core.Proxy.CSMessage F_GetMessage(System.Int32 pId)",
        markers=(
            "Method: Core.Proxy.CSMessage F_GetMessage(System.Int32 pId)",
            "Call 0x1F15F08, X0, X1",
            "Call 0x1F15B98, X0, X1",
            "Call Activator.CreateInstance, X0",
        ),
    )
    _append_matching_evidence_in_section(
        evidence_rows,
        path=cs_message_pool_isil,
        export_base=export_base,
        kind="cpp2il_isil",
        stage="08-csmessagepool-send-handler",
        section_marker="Method: System.Void F_SendHandler(System.Int32 pId, Core.Proxy.CSMessage pInfo)",
        markers=(
            "Method: System.Void F_SendHandler(System.Int32 pId, Core.Proxy.CSMessage pInfo)",
            "Call 0x1F15F08, X0, X1",
            "Call 0x1F15B98, X0, X1",
            "Move X1, X19",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=message_pool_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="09-lua-messagepool",
        markers=(
            "function _M:F_Register(id,messageClass,handlerClass)",
            "SocketBridge.RegisterLuaPro(id)",
            "function _M:F_SendHandler(pId,pInfo)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=socket_manager_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="10-lua-socketmanager",
        markers=(
            "function _M.GetMessageFromPools(self,pSendInfo)",
            "msg=MessagePool.Inst_get():F_GetMessage(proId)",
            "MessagePool.Inst:F_SendHandler(msg:getId(),msg)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=login_net_logic_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="11-lua-login-handlers",
        markers=(
            "_MessagePool.Inst_get():F_Register(_SM_Login:getId(),typeof(_SM_Login),function(msg)",
            "_MessagePool.Inst_get():F_Register(_SM_ProtoHash:getId(),typeof(_SM_ProtoHash),function(msg)",
            "function _M.SM_ProtoHashFun(msg)",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=sm_proto_hash_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="12-lua-sm-protohash",
        markers=(
            "function _M.reading(self)",
            "self.hash=self:readInt()",
            "self.version=self:readString()",
            "return 20014",
        ),
    )
    _append_matching_evidence(
        evidence_rows,
        path=sm_login_lua,
        export_base=export_base,
        kind="lua_text_asset",
        stage="13-lua-sm-login",
        markers=(
            "function _M.reading(self)",
            "self.accountId=self:readString()",
            "self.token=self:readString()",
            "self.timeZone=self:readInt()",
            "return 20002",
        ),
    )

    checks = {
        "process_receive_reads_4_byte_length_head": _evidence_has_stage(
            evidence_rows,
            "01-bytesocket-receive-head",
            "Call Socket.Receive, X0, X1, X2, X3",
        )
        and _evidence_has_stage(evidence_rows, "01-bytesocket-receive-head", "Call ByteUtil.ReadInt, X0, X1, X2"),
        "process_receive_reads_body_and_calls_readpackage": _evidence_has_stage(
            evidence_rows,
            "01-bytesocket-receive-head",
            "Call Socket.Receive, X0, X1, X2, X3, X4",
        )
        and _evidence_has_stage(evidence_rows, "01-bytesocket-receive-head", "Call ByteSocket.ReadPackage, X0"),
        "readpackage_wraps_lusuo_stream_and_reads_header_ints": _evidence_has_stage(
            evidence_rows,
            "02-bytesocket-readpackage-stream",
            "Call LusuoStreamQuick..ctor, X0, X1, X2, X3, X4",
        )
        and _evidence_has_stage(evidence_rows, "02-bytesocket-readpackage-stream", "Call LusuoStreamQuick.ReadInt, X0, X1"),
        "readpackage_detects_lua_messages_and_reads_payload": _evidence_has_stage(
            evidence_rows,
            "02-bytesocket-readpackage-stream",
            "Call PoolMessageManage.IsLuaMessage, X0",
        )
        and _evidence_has_stage(evidence_rows, "02-bytesocket-readpackage-stream", "Call PoolMessageManage.read, X0, X1"),
        "poolmessage_resolves_message_class": _evidence_has_stage(
            evidence_rows,
            "03-poolmessage-read",
            "Call CSMessagePool.F_GetMessage, X0, X1",
        ),
        "csmessagepool_constructs_and_dispatches_message": _evidence_has_stage(
            evidence_rows,
            "07-csmessagepool-get-message",
            "Call Activator.CreateInstance, X0",
        )
        and _evidence_has_stage(evidence_rows, "08-csmessagepool-send-handler", "Move X1, X19"),
        "lua_messagepool_registers_socketbridge_pro_ids": _evidence_has_stage(
            evidence_rows,
            "09-lua-messagepool",
            "SocketBridge.RegisterLuaPro(id)",
        ),
        "lua_login_registers_sm_login_and_protohash": _evidence_has_stage(
            evidence_rows,
            "11-lua-login-handlers",
            "_MessagePool.Inst_get():F_Register(_SM_Login:getId(),typeof(_SM_Login),function(msg)",
        )
        and _evidence_has_stage(
            evidence_rows,
            "11-lua-login-handlers",
            "_MessagePool.Inst_get():F_Register(_SM_ProtoHash:getId(),typeof(_SM_ProtoHash),function(msg)",
        ),
        "lua_packet_ids_and_reading_fields_confirm_examples": _evidence_has_stage(
            evidence_rows,
            "12-lua-sm-protohash",
            "return 20014",
        )
        and _evidence_has_stage(evidence_rows, "13-lua-sm-login", "return 20002"),
    }

    fields = ["stage", "kind", "source", "line", "marker", "snippet"]
    evidence_count = _write_tsv(output_dir / "cpp2il_socket_receive_dispatch_evidence.tsv", fields, evidence_rows)
    _write_cpp2il_socket_receive_dispatch_markdown(
        output_dir / "cpp2il_socket_receive_dispatch_report.md",
        export_base=export_base,
        output_dir=output_dir,
        evidence_rows=evidence_rows,
        checks=checks,
    )

    result = {
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "report_path": str(output_dir / "cpp2il_socket_receive_dispatch_report.md"),
        "evidence_path": str(output_dir / "cpp2il_socket_receive_dispatch_evidence.tsv"),
        "confirmed": all(checks.values()),
        "checks": checks,
        "evidence_count": evidence_count,
        "sources": {
            "bytesocket_isil": str(bytesocket_isil),
            "pool_message_manage_isil": str(pool_message_manage_isil),
            "cs_message_pool_isil": str(cs_message_pool_isil),
            "message_pool_lua": str(message_pool_lua) if message_pool_lua else "",
            "socket_manager_lua": str(socket_manager_lua) if socket_manager_lua else "",
            "login_net_logic_lua": str(login_net_logic_lua) if login_net_logic_lua else "",
            "sm_proto_hash_lua": str(sm_proto_hash_lua) if sm_proto_hash_lua else "",
            "sm_login_lua": str(sm_login_lua) if sm_login_lua else "",
        },
    }
    (output_dir / "cpp2il_socket_receive_dispatch_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
