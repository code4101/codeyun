from __future__ import annotations

import csv
import json
import os
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.fanxiu_apk_static import APK_INDEX_DEFAULT_KEYWORDS, resolve_fanxiu_apk_unpacked_root
from backend.core.fanxiu_resources import FanxiuResourceError, resolve_fanxiu_export_root


IL2CPP_METADATA_MAGIC = 0xFAB11BAF
IL2CPP_METADATA_HEADER_TABLES = (
    "string_literal",
    "string_literal_data",
    "string",
    "events",
    "properties",
    "methods",
    "parameter_default_values",
    "field_default_values",
    "field_and_parameter_default_value_data",
    "field_marshaled_sizes",
    "parameters",
    "fields",
    "generic_parameters",
    "generic_parameter_constraints",
    "generic_containers",
    "nested_types",
    "interfaces",
    "vtable_methods",
    "interface_offsets",
    "type_definitions",
    "rgctx_entries",
    "images",
    "assemblies",
    "metadata_usage_lists",
    "metadata_usage_pairs",
    "field_refs",
    "referenced_assemblies",
    "attributes_info",
    "attribute_types",
    "unresolved_virtual_call_parameter_types",
    "unresolved_virtual_call_parameter_ranges",
    "windows_runtime_type_names",
)

IL2CPP_TYPE_DEFINITION_SIZE = 92
IL2CPP_METHOD_DEFINITION_SIZE = 32
IL2CPP_FIELD_DEFINITION_SIZE = 12
IL2CPP_PARAMETER_DEFINITION_SIZE = 12
IL2CPP_STRING_LITERAL_SIZE = 8
IL2CPP_HOT_UPDATE_KEYWORDS = (
    "GameResDownLoad",
    "GameResDownload",
    "AssetBundleEncryptStream",
    "LuaHotfixManager",
    "HotFix",
    "Hotfix",
    "HotGameStart",
    "HotFixVerConfig",
    "LuaBridge_Load",
    "LuaBridge.Load",
    "AssetConfigItem",
    "DownloadPackage",
    "F_CheckIsResUpdate",
    "ContainsPackage",
    "ContainsAsset",
    "GetDownloadProgress",
    "V_assetBundleEncryptStream",
)
IL2CPP_HOT_UPDATE_STRING_KEYWORDS = (
    "assetbundle",
    "download",
    "encrypt",
    "decrypt",
    "filelist",
    "hotfix",
    "http",
    "https",
    "lua",
    "md5",
    "patch",
    "resource",
    "version",
    "zip",
    "cdn",
)


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


def _read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise FanxiuResourceError(f"IL2CPP metadata 偏移越界：{offset}")
    return struct.unpack_from("<I", data, offset)[0]


def _token_hex(value: int) -> str:
    return f"0x{value & 0xFFFFFFFF:08X}"


def resolve_fanxiu_il2cpp_metadata_path(
    *,
    metadata_path: str | os.PathLike[str] | None = None,
    apk_root: str | os.PathLike[str] | None = None,
) -> Path:
    if metadata_path:
        path = Path(metadata_path).expanduser().resolve()
    else:
        root = resolve_fanxiu_apk_unpacked_root(apk_root)
        path = root / "assets" / "bin" / "Data" / "Managed" / "Metadata" / "global-metadata.dat"
    if not path.exists():
        raise FanxiuResourceError(f"IL2CPP metadata 文件不存在：{path}")
    if not path.is_file():
        raise FanxiuResourceError(f"IL2CPP metadata 路径不是文件：{path}")
    return path


def _read_header(data: bytes) -> dict[str, Any]:
    if len(data) < 16:
        raise FanxiuResourceError("IL2CPP metadata 文件过短")
    sanity = _read_u32(data, 0)
    version = _read_u32(data, 4)
    if sanity != IL2CPP_METADATA_MAGIC:
        raise FanxiuResourceError(f"IL2CPP metadata magic 不匹配：0x{sanity:08X}")

    first_table_offset = _read_u32(data, 8)
    if first_table_offset < 16 or first_table_offset > len(data):
        raise FanxiuResourceError(f"IL2CPP metadata header 尺寸异常：{first_table_offset}")
    pair_count = (first_table_offset - 8) // 8
    tables: dict[str, dict[str, int]] = {}
    warnings: list[str] = []
    for index in range(pair_count):
        offset, size = struct.unpack_from("<II", data, 8 + index * 8)
        name = IL2CPP_METADATA_HEADER_TABLES[index] if index < len(IL2CPP_METADATA_HEADER_TABLES) else f"unknown_{index}"
        tables[name] = {"offset": offset, "size": size, "index": index}
        if size and (offset >= len(data) or offset + size > len(data)):
            warnings.append(f"{name} 表范围越界：offset={offset}, size={size}")

    return {
        "sanity": sanity,
        "version": version,
        "header_size": first_table_offset,
        "table_count": pair_count,
        "tables": tables,
        "warnings": warnings,
    }


def _table(tables: dict[str, dict[str, int]], name: str) -> tuple[int, int]:
    item = tables.get(name) or {"offset": 0, "size": 0}
    return item["offset"], item["size"]


def _make_string_reader(data: bytes, string_offset: int, string_size: int) -> Callable[[int], str]:
    cache: dict[int, str] = {}
    string_end = string_offset + string_size

    def read_string(index: int) -> str:
        if index in cache:
            return cache[index]
        if index < 0 or index >= string_size:
            value = ""
        else:
            absolute = string_offset + index
            end = data.find(b"\x00", absolute, string_end)
            if end < 0:
                end = string_end
            value = data[absolute:end].decode("utf-8", errors="replace")
        cache[index] = value
        return value

    return read_string


def _iter_metadata_strings(data: bytes, string_offset: int, string_size: int) -> Iterable[dict[str, object]]:
    cursor = 0
    string_end = string_offset + string_size
    while cursor < string_size:
        absolute = string_offset + cursor
        end = data.find(b"\x00", absolute, string_end)
        if end < 0:
            end = string_end
        value = data[absolute:end].decode("utf-8", errors="replace")
        if value:
            yield {"string_index": cursor, "value": value}
        next_cursor = end - string_offset + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor


def _iter_string_literals(
    data: bytes,
    literal_offset: int,
    literal_size: int,
    literal_data_offset: int,
    literal_data_size: int,
) -> Iterable[dict[str, object]]:
    literal_count = literal_size // IL2CPP_STRING_LITERAL_SIZE
    literal_data_end = literal_data_offset + literal_data_size
    for index in range(literal_count):
        length, data_index = struct.unpack_from("<II", data, literal_offset + index * IL2CPP_STRING_LITERAL_SIZE)
        start = literal_data_offset + data_index
        end = min(start + length, literal_data_end)
        value = data[start:end].decode("utf-8", errors="replace") if start <= literal_data_end else ""
        yield {
            "index": index,
            "length": length,
            "data_index": data_index,
            "value": value,
        }


def _parse_type_definitions(
    data: bytes,
    offset: int,
    size: int,
    read_string: Callable[[int], str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    count = size // IL2CPP_TYPE_DEFINITION_SIZE
    type_fmt = "<iiiiiiiiIiiiiiiiiHHHHHHHHII"
    for index in range(count):
        values = struct.unpack_from(type_fmt, data, offset + index * IL2CPP_TYPE_DEFINITION_SIZE)
        (
            name_index,
            namespace_index,
            byval_type_index,
            byref_type_index,
            declaring_type_index,
            parent_index,
            element_type_index,
            generic_container_index,
            flags,
            field_start,
            method_start,
            event_start,
            property_start,
            nested_types_start,
            interfaces_start,
            vtable_start,
            interface_offsets_start,
            method_count,
            property_count,
            field_count,
            event_count,
            nested_type_count,
            vtable_count,
            interfaces_count,
            interface_offsets_count,
            bitfield,
            token,
        ) = values
        name = read_string(name_index)
        namespace = read_string(namespace_index)
        full_name = f"{namespace}.{name}" if namespace else name
        rows.append(
            {
                "index": index,
                "namespace": namespace,
                "name": name,
                "full_name": full_name,
                "flags": f"0x{flags:08X}",
                "bitfield": f"0x{bitfield:08X}",
                "token": _token_hex(token),
                "byval_type_index": byval_type_index,
                "byref_type_index": byref_type_index,
                "declaring_type_index": declaring_type_index,
                "parent_index": parent_index,
                "element_type_index": element_type_index,
                "generic_container_index": generic_container_index,
                "field_start": field_start,
                "field_count": field_count,
                "method_start": method_start,
                "method_count": method_count,
                "event_start": event_start,
                "event_count": event_count,
                "property_start": property_start,
                "property_count": property_count,
                "nested_types_start": nested_types_start,
                "nested_type_count": nested_type_count,
                "interfaces_start": interfaces_start,
                "interfaces_count": interfaces_count,
                "vtable_start": vtable_start,
                "vtable_count": vtable_count,
                "interface_offsets_start": interface_offsets_start,
                "interface_offsets_count": interface_offsets_count,
            }
        )
    return rows


def _owner_by_range(type_rows: list[dict[str, object]], start_key: str, count_key: str, total_count: int) -> dict[int, str]:
    owners: dict[int, str] = {}
    for row in type_rows:
        start = int(row[start_key])
        count = int(row[count_key])
        if start < 0 or count <= 0:
            continue
        for index in range(start, min(start + count, total_count)):
            owners[index] = str(row["full_name"])
    return owners


def _parse_method_definitions(
    data: bytes,
    offset: int,
    size: int,
    read_string: Callable[[int], str],
    type_rows: list[dict[str, object]],
    parameter_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    method_count = size // IL2CPP_METHOD_DEFINITION_SIZE
    owner_by_method = _owner_by_range(type_rows, "method_start", "method_count", method_count)
    rows: list[dict[str, object]] = []
    method_fmt = "<iiiiiIHHHH"
    for index in range(method_count):
        (
            name_index,
            declaring_type,
            return_type,
            parameter_start,
            generic_container_index,
            token,
            flags,
            iflags,
            slot,
            parameter_count,
        ) = struct.unpack_from(method_fmt, data, offset + index * IL2CPP_METHOD_DEFINITION_SIZE)
        name = read_string(name_index)
        owner = owner_by_method.get(index)
        if not owner and 0 <= declaring_type < len(type_rows):
            owner = str(type_rows[declaring_type]["full_name"])
        parameters = _method_parameter_signature(parameter_rows, parameter_start, parameter_count)
        rows.append(
            {
                "index": index,
                "owner": owner or "",
                "name": name,
                "qualified_name": f"{owner}.{name}" if owner and name else name,
                "parameters": parameters,
                "declaring_type": declaring_type,
                "return_type": return_type,
                "parameter_start": parameter_start,
                "parameter_count": parameter_count,
                "generic_container_index": generic_container_index,
                "token": _token_hex(token),
                "flags": flags,
                "iflags": iflags,
                "slot": slot,
            }
        )
    return rows


def _parse_parameter_definitions(
    data: bytes,
    offset: int,
    size: int,
    read_string: Callable[[int], str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    count = size // IL2CPP_PARAMETER_DEFINITION_SIZE
    for index in range(count):
        name_index, token, type_index = struct.unpack_from("<iIi", data, offset + index * IL2CPP_PARAMETER_DEFINITION_SIZE)
        rows.append(
            {
                "index": index,
                "name": read_string(name_index),
                "type_index": type_index,
                "token": _token_hex(token),
            }
        )
    return rows


def _method_parameter_signature(parameter_rows: list[dict[str, object]], start: int, count: int) -> str:
    if start < 0 or count <= 0:
        return ""
    parts: list[str] = []
    for index in range(start, min(start + count, len(parameter_rows))):
        row = parameter_rows[index]
        name = str(row.get("name") or f"arg{index - start}")
        type_index = row.get("type_index", "")
        parts.append(f"{name}:type#{type_index}")
    return ", ".join(parts)


def _parse_field_definitions(
    data: bytes,
    offset: int,
    size: int,
    read_string: Callable[[int], str],
    type_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    field_count = size // IL2CPP_FIELD_DEFINITION_SIZE
    owner_by_field = _owner_by_range(type_rows, "field_start", "field_count", field_count)
    rows: list[dict[str, object]] = []
    for index in range(field_count):
        name_index, type_index, token = struct.unpack_from("<iiI", data, offset + index * IL2CPP_FIELD_DEFINITION_SIZE)
        name = read_string(name_index)
        owner = owner_by_field.get(index, "")
        rows.append(
            {
                "index": index,
                "owner": owner,
                "name": name,
                "qualified_name": f"{owner}.{name}" if owner and name else name,
                "type_index": type_index,
                "token": _token_hex(token),
            }
        )
    return rows


def _keyword_hits(
    *,
    rows_by_kind: dict[str, Iterable[dict[str, object]]],
    value_field: str,
    keywords: tuple[str, ...],
    limit: int,
) -> Iterable[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    count = 0
    for kind, rows in rows_by_kind.items():
        for row in rows:
            if count >= limit:
                return
            value = str(row.get(value_field) or row.get("value") or "")
            normalized = value.lower()
            for keyword in keywords:
                if keyword.lower() not in normalized:
                    continue
                key = (kind, keyword, value)
                if key in seen:
                    continue
                seen.add(key)
                yield {
                    "kind": kind,
                    "keyword": keyword,
                    "index": row.get("index", row.get("string_index", "")),
                    "value": value,
                }
                count += 1
                if count >= limit:
                    return


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _matches_any(value: str, keywords: tuple[str, ...]) -> bool:
    normalized = value.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _hot_update_role(value: str) -> str:
    lower = value.lower()
    if "assetbundleencryptstream" in lower:
        return "asset-bundle-encryption"
    if "gameresdownload" in lower or "gameresdownload" in lower.replace("downl", "downl"):
        return "resource-download"
    if "hotfix" in lower or "luahotfix" in lower:
        return "lua-hotfix"
    if "luabridge" in lower or lower.endswith("bridgewrap"):
        return "lua-bridge"
    if "assetconfigitem" in lower:
        return "asset-config"
    return "candidate"


def _write_hot_update_markdown(
    path: Path,
    *,
    summary: dict[str, Any],
    type_rows: list[dict[str, str]],
    method_rows: list[dict[str, str]],
    field_rows: list[dict[str, str]],
    string_rows: list[dict[str, str]],
) -> None:
    by_owner: dict[str, list[dict[str, str]]] = {}
    for row in method_rows:
        by_owner.setdefault(row.get("owner", ""), []).append(row)

    lines = [
        "# 凡修 IL2CPP 热更链路聚焦报告",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- metadata：`{summary['metadata_path']}`",
        f"- 类型候选：{summary['counts']['types']}",
        f"- 方法候选：{summary['counts']['methods']}",
        f"- 字段候选：{summary['counts']['fields']}",
        f"- 字符串候选：{summary['counts']['strings']}",
        "",
        "## 关键类型",
        "",
    ]
    for row in type_rows[:80]:
        lines.append(
            f"- `{row.get('full_name', '')}` ({row.get('role', '')}) "
            f"methods={row.get('method_count', '')}, fields={row.get('field_count', '')}, token={row.get('token', '')}"
        )

    lines.extend(["", "## 关键方法", ""])
    for owner in sorted(by_owner)[:80]:
        methods = by_owner[owner]
        lines.append(f"### {owner or '<unknown>'}")
        for row in methods[:30]:
            parameters = row.get("parameters", "")
            suffix = f" ({parameters})" if parameters else ""
            lines.append(f"- `{row.get('name', '')}`{suffix} token={row.get('token', '')} params={row.get('parameter_count', '')}")
        lines.append("")

    if field_rows:
        lines.extend(["## 关键字段", ""])
        for row in field_rows[:120]:
            lines.append(f"- `{row.get('qualified_name', '')}` token={row.get('token', '')} type_index={row.get('type_index', '')}")
        lines.append("")

    if string_rows:
        lines.extend(["## 相关字符串摘样", ""])
        for row in string_rows[:160]:
            value = _clean_tsv_cell(row.get("value", ""), limit=180)
            lines.append(f"- {row.get('kind', '')}:{row.get('index', '')} `{value}`")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_fanxiu_il2cpp_metadata_probe(
    *,
    metadata_path: str | os.PathLike[str] | None = None,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    keywords: Iterable[str] | None = None,
    keyword_hit_limit: int = 30000,
) -> dict[str, Any]:
    path = resolve_fanxiu_il2cpp_metadata_path(metadata_path=metadata_path, apk_root=apk_root)
    export_base = resolve_fanxiu_export_root(export_root)
    output_dir = (export_base / "apk_static_index").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    data = path.read_bytes()
    header = _read_header(data)
    tables = header["tables"]
    string_offset, string_size = _table(tables, "string")
    if not string_size:
        raise FanxiuResourceError("IL2CPP metadata 缺少 string 表")
    read_string = _make_string_reader(data, string_offset, string_size)

    type_offset, type_size = _table(tables, "type_definitions")
    method_offset, method_size = _table(tables, "methods")
    field_offset, field_size = _table(tables, "fields")
    parameter_offset, parameter_size = _table(tables, "parameters")
    literal_offset, literal_size = _table(tables, "string_literal")
    literal_data_offset, literal_data_size = _table(tables, "string_literal_data")

    warnings = list(header["warnings"])
    if type_size % IL2CPP_TYPE_DEFINITION_SIZE:
        warnings.append(f"type_definitions 表大小不是 {IL2CPP_TYPE_DEFINITION_SIZE} 的整数倍：{type_size}")
    if method_size % IL2CPP_METHOD_DEFINITION_SIZE:
        warnings.append(f"methods 表大小不是 {IL2CPP_METHOD_DEFINITION_SIZE} 的整数倍：{method_size}")
    if field_size % IL2CPP_FIELD_DEFINITION_SIZE:
        warnings.append(f"fields 表大小不是 {IL2CPP_FIELD_DEFINITION_SIZE} 的整数倍：{field_size}")
    if parameter_size % IL2CPP_PARAMETER_DEFINITION_SIZE:
        warnings.append(f"parameters 表大小不是 {IL2CPP_PARAMETER_DEFINITION_SIZE} 的整数倍：{parameter_size}")

    type_rows = _parse_type_definitions(data, type_offset, type_size, read_string)
    parameter_rows = _parse_parameter_definitions(data, parameter_offset, parameter_size, read_string)
    method_rows = _parse_method_definitions(data, method_offset, method_size, read_string, type_rows, parameter_rows)
    field_rows = _parse_field_definitions(data, field_offset, field_size, read_string, type_rows)
    string_rows = list(_iter_metadata_strings(data, string_offset, string_size))
    literal_rows = list(_iter_string_literals(data, literal_offset, literal_size, literal_data_offset, literal_data_size))

    type_fields = [
        "index",
        "namespace",
        "name",
        "full_name",
        "flags",
        "bitfield",
        "token",
        "field_start",
        "field_count",
        "method_start",
        "method_count",
        "event_start",
        "event_count",
        "property_start",
        "property_count",
        "nested_types_start",
        "nested_type_count",
        "interfaces_start",
        "interfaces_count",
        "vtable_start",
        "vtable_count",
        "interface_offsets_start",
        "interface_offsets_count",
        "parent_index",
        "declaring_type_index",
        "generic_container_index",
        "byval_type_index",
        "byref_type_index",
        "element_type_index",
    ]
    method_fields = [
        "index",
        "owner",
        "name",
        "qualified_name",
        "parameters",
        "declaring_type",
        "return_type",
        "parameter_start",
        "parameter_count",
        "generic_container_index",
        "token",
        "flags",
        "iflags",
        "slot",
    ]
    field_fields = ["index", "owner", "name", "qualified_name", "type_index", "token"]
    parameter_fields = ["index", "name", "type_index", "token"]
    string_fields = ["string_index", "value"]
    literal_fields = ["index", "length", "data_index", "value"]
    keyword_fields = ["kind", "keyword", "index", "value"]

    type_count = _write_tsv(output_dir / "il2cpp_types.tsv", type_fields, type_rows)
    method_count = _write_tsv(output_dir / "il2cpp_methods.tsv", method_fields, method_rows)
    field_count = _write_tsv(output_dir / "il2cpp_fields.tsv", field_fields, field_rows)
    parameter_count = _write_tsv(output_dir / "il2cpp_parameters.tsv", parameter_fields, parameter_rows)
    metadata_string_count = _write_tsv(output_dir / "il2cpp_strings.tsv", string_fields, string_rows)
    literal_count = _write_tsv(output_dir / "il2cpp_string_literals.tsv", literal_fields, literal_rows)

    normalized_keywords = tuple(dict.fromkeys(str(item) for item in (keywords or APK_INDEX_DEFAULT_KEYWORDS) if str(item).strip()))
    keyword_rows = list(
        _keyword_hits(
            rows_by_kind={
                "type": type_rows,
                "method": method_rows,
                "field": field_rows,
                "metadata_string": string_rows,
                "string_literal": literal_rows,
            },
            value_field="qualified_name",
            keywords=normalized_keywords,
            limit=keyword_hit_limit,
        )
    )
    keyword_count = _write_tsv(output_dir / "il2cpp_keyword_hits.tsv", keyword_fields, keyword_rows)

    result = {
        "metadata_path": str(path),
        "export_root": str(export_base),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "magic": f"0x{header['sanity']:08X}",
        "version": header["version"],
        "header_size": header["header_size"],
        "warnings": warnings,
        "tables": header["tables"],
        "counts": {
            "types": type_count,
            "methods": method_count,
            "fields": field_count,
            "parameters": parameter_count,
            "metadata_strings": metadata_string_count,
            "string_literals": literal_count,
            "keyword_hits": keyword_count,
        },
        "outputs": {
            "summary": str(output_dir / "il2cpp_metadata_summary.json"),
            "types": str(output_dir / "il2cpp_types.tsv"),
            "methods": str(output_dir / "il2cpp_methods.tsv"),
            "fields": str(output_dir / "il2cpp_fields.tsv"),
            "parameters": str(output_dir / "il2cpp_parameters.tsv"),
            "strings": str(output_dir / "il2cpp_strings.tsv"),
            "string_literals": str(output_dir / "il2cpp_string_literals.tsv"),
            "keyword_hits": str(output_dir / "il2cpp_keyword_hits.tsv"),
        },
    }
    (output_dir / "il2cpp_metadata_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def build_fanxiu_il2cpp_hot_update_report(
    *,
    metadata_path: str | os.PathLike[str] | None = None,
    apk_root: str | os.PathLike[str] | None = None,
    export_root: str | os.PathLike[str] | None = None,
    keywords: Iterable[str] | None = None,
    string_keywords: Iterable[str] | None = None,
    type_limit: int = 500,
    string_limit: int = 500,
) -> dict[str, Any]:
    probe = build_fanxiu_il2cpp_metadata_probe(
        metadata_path=metadata_path,
        apk_root=apk_root,
        export_root=export_root,
        keywords=keywords or IL2CPP_HOT_UPDATE_KEYWORDS,
    )
    output_dir = Path(probe["output_dir"])
    normalized_keywords = tuple(dict.fromkeys(str(item) for item in (keywords or IL2CPP_HOT_UPDATE_KEYWORDS) if str(item).strip()))
    normalized_string_keywords = tuple(
        dict.fromkeys(str(item) for item in (string_keywords or IL2CPP_HOT_UPDATE_STRING_KEYWORDS) if str(item).strip())
    )

    all_types = _read_tsv(output_dir / "il2cpp_types.tsv")
    all_methods = _read_tsv(output_dir / "il2cpp_methods.tsv")
    all_fields = _read_tsv(output_dir / "il2cpp_fields.tsv")
    all_strings = _read_tsv(output_dir / "il2cpp_strings.tsv")
    all_literals = _read_tsv(output_dir / "il2cpp_string_literals.tsv")

    selected_types: list[dict[str, str]] = []
    selected_type_names: set[str] = set()
    for row in all_types:
        full_name = row.get("full_name", "")
        if not _matches_any(full_name, normalized_keywords):
            continue
        item = dict(row)
        item["role"] = _hot_update_role(full_name)
        selected_types.append(item)
        selected_type_names.add(full_name)
        if len(selected_types) >= type_limit:
            break

    selected_methods: list[dict[str, str]] = []
    for row in all_methods:
        owner = row.get("owner", "")
        qualified_name = row.get("qualified_name", "")
        if owner not in selected_type_names and not _matches_any(qualified_name, normalized_keywords):
            continue
        item = dict(row)
        item["role"] = _hot_update_role(qualified_name)
        selected_methods.append(item)

    selected_fields: list[dict[str, str]] = []
    for row in all_fields:
        owner = row.get("owner", "")
        qualified_name = row.get("qualified_name", "")
        if owner not in selected_type_names and not _matches_any(qualified_name, normalized_keywords):
            continue
        item = dict(row)
        item["role"] = _hot_update_role(qualified_name)
        selected_fields.append(item)

    selected_strings: list[dict[str, str]] = []
    for kind, rows in (("metadata_string", all_strings), ("string_literal", all_literals)):
        for row in rows:
            value = row.get("value", "")
            if not _matches_any(value, normalized_string_keywords):
                continue
            selected_strings.append(
                {
                    "kind": kind,
                    "index": row.get("string_index", row.get("index", "")),
                    "keyword_role": _hot_update_role(value),
                    "value": value,
                }
            )
            if len(selected_strings) >= string_limit:
                break
        if len(selected_strings) >= string_limit:
            break

    type_fields = [
        "role",
        "index",
        "namespace",
        "name",
        "full_name",
        "token",
        "method_start",
        "method_count",
        "field_start",
        "field_count",
    ]
    method_fields = [
        "role",
        "index",
        "owner",
        "name",
        "qualified_name",
        "token",
        "parameters",
        "parameter_start",
        "parameter_count",
        "return_type",
    ]
    field_fields = ["role", "index", "owner", "name", "qualified_name", "token", "type_index"]
    string_fields = ["kind", "index", "keyword_role", "value"]
    type_count = _write_tsv(output_dir / "hot_update_types.tsv", type_fields, selected_types)
    method_count = _write_tsv(output_dir / "hot_update_methods.tsv", method_fields, selected_methods)
    field_count = _write_tsv(output_dir / "hot_update_fields.tsv", field_fields, selected_fields)
    string_count = _write_tsv(output_dir / "hot_update_strings.tsv", string_fields, selected_strings)

    result = {
        "metadata_path": probe["metadata_path"],
        "export_root": probe["export_root"],
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "keywords": list(normalized_keywords),
        "string_keywords": list(normalized_string_keywords),
        "counts": {
            "types": type_count,
            "methods": method_count,
            "fields": field_count,
            "strings": string_count,
        },
        "outputs": {
            "summary": str(output_dir / "hot_update_report.json"),
            "markdown": str(output_dir / "hot_update_report.md"),
            "types": str(output_dir / "hot_update_types.tsv"),
            "methods": str(output_dir / "hot_update_methods.tsv"),
            "fields": str(output_dir / "hot_update_fields.tsv"),
            "strings": str(output_dir / "hot_update_strings.tsv"),
        },
    }
    _write_hot_update_markdown(
        output_dir / "hot_update_report.md",
        summary=result,
        type_rows=selected_types,
        method_rows=selected_methods,
        field_rows=selected_fields,
        string_rows=selected_strings,
    )
    (output_dir / "hot_update_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
