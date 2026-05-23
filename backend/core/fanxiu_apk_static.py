from __future__ import annotations

import csv
import json
import os
import re
import struct
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root, resolve_fanxiu_resource_root


FANXIU_APK_UNPACKED_ROOT_ENV = "FANXIU_APK_UNPACKED_ROOT"
DEFAULT_FANXIU_APK_UNPACKED_ROOT = Path(
    r"D:\TapTap\Support\android_emulator\games\308550\apk\1023295_unpacked"
)

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
